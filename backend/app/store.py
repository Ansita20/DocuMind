"""Document registry persisted to disk.

Layout: DATA_DIR/docs/<doc_id>/{meta.json, chunks.json, original.<ext>}

Ingestion runs in worker threads while request handlers read on the event loop,
so every mutation goes through a lock.
"""

import json
import shutil
import threading
from pathlib import Path

from .config import DATA_DIR
from .schemas import Chunk, DocumentRecord

DOCS_DIR = DATA_DIR / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)


class DocumentStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._docs: dict[str, DocumentRecord] = {}
        self._chunks: dict[str, list[Chunk]] = {}
        self._load()

    def _load(self) -> None:
        for meta_path in DOCS_DIR.glob("*/meta.json"):
            doc = DocumentRecord.model_validate_json(meta_path.read_text())
            if doc.status not in ("ready", "failed"):
                # The server stopped mid-ingestion; the pipeline is not resumable.
                doc.status, doc.error, doc.detail = "failed", "Interrupted by server restart", "Re-upload to retry"
            self._docs[doc.id] = doc
            chunks_path = meta_path.parent / "chunks.json"
            if doc.status == "ready" and chunks_path.exists():
                raw = json.loads(chunks_path.read_text())
                self._chunks[doc.id] = [Chunk.model_validate(c) for c in raw]

    def doc_dir(self, doc_id: str) -> Path:
        return DOCS_DIR / doc_id

    def original_path(self, doc_id: str) -> Path | None:
        matches = list(self.doc_dir(doc_id).glob("original.*"))
        return matches[0] if matches else None

    def add(self, doc: DocumentRecord) -> None:
        with self._lock:
            self._docs[doc.id] = doc
            self._persist_meta(doc)

    def update(self, doc_id: str, **fields) -> DocumentRecord:
        with self._lock:
            doc = self._docs[doc_id].model_copy(update=fields)
            self._docs[doc_id] = doc
            self._persist_meta(doc)
            return doc

    def set_chunks(self, doc_id: str, chunks: list[Chunk]) -> None:
        with self._lock:
            self._chunks[doc_id] = chunks
            path = self.doc_dir(doc_id) / "chunks.json"
            path.write_text(json.dumps([c.model_dump() for c in chunks]))

    def get(self, doc_id: str) -> DocumentRecord | None:
        with self._lock:
            return self._docs.get(doc_id)

    def all(self) -> list[DocumentRecord]:
        with self._lock:
            return sorted(self._docs.values(), key=lambda d: d.created_at, reverse=True)

    def ready_chunks(self) -> dict[str, list[Chunk]]:
        with self._lock:
            return {d: c for d, c in self._chunks.items() if self._docs[d].status == "ready"}

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            if self._docs.pop(doc_id, None) is None:
                return False
            self._chunks.pop(doc_id, None)
            shutil.rmtree(self.doc_dir(doc_id), ignore_errors=True)
            return True

    def _persist_meta(self, doc: DocumentRecord) -> None:
        d = self.doc_dir(doc.id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "meta.json.tmp"
        tmp.write_text(doc.model_dump_json())
        tmp.replace(d / "meta.json")


store = DocumentStore()
