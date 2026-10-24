"""Document registry on top of Storage.

Layout: docs/<doc_id>/{original.<ext>, chunks.json, meta.json}

meta.json is written last, so a document appears in listings only once it is
complete. Reads are cached per (path, version) so a warm instance only fetches
objects that changed since its last request.
"""

import asyncio
import json
import mimetypes

from .config import DATA_DIR
from .schemas import Chunk, DocumentRecord
from .storage import make_storage

PREFIX = "docs/"


class StorageNotConfigured(RuntimeError):
    pass


class DocumentStore:
    def __init__(self) -> None:
        self.storage = make_storage(DATA_DIR)
        self._cache: dict[tuple[str, str], object] = {}

    @property
    def backend(self) -> str:
        return self.storage.name if self.storage else "missing"

    def _require(self):
        if self.storage is None:
            raise StorageNotConfigured(
                "No Vercel Blob store is connected. In the Vercel dashboard open Storage → Create → Blob, "
                "connect it to this project, then redeploy."
            )
        return self.storage

    async def _read_json(self, path: str, version: str, parse):
        key = (path, version)
        if key not in self._cache:
            raw = await self._require().get(path)
            self._cache[key] = parse(json.loads(raw)) if raw is not None else None
        return self._cache[key]

    async def _snapshot(self) -> dict[str, str]:
        return await self._require().list(PREFIX)

    async def all(self, snap: dict[str, str] | None = None) -> list[DocumentRecord]:
        snap = snap if snap is not None else await self._snapshot()
        metas = [p for p in snap if p.endswith("/meta.json")]
        docs = await asyncio.gather(*(self._read_json(p, snap[p], DocumentRecord.model_validate) for p in metas))
        return sorted((d for d in docs if d), key=lambda d: d.created_at, reverse=True)

    async def get(self, doc_id: str) -> DocumentRecord | None:
        return next((d for d in await self.all() if d.id == doc_id), None)

    async def ready_chunks(self) -> dict[str, list[Chunk]]:
        snap = await self._snapshot()
        ready = [d.id for d in await self.all(snap) if d.status == "ready"]
        paths = [f"{PREFIX}{d}/chunks.json" for d in ready if f"{PREFIX}{d}/chunks.json" in snap]
        lists = await asyncio.gather(
            *(self._read_json(p, snap[p], lambda raw: [Chunk.model_validate(c) for c in raw]) for p in paths)
        )
        return {p.split("/")[1]: chunks or [] for p, chunks in zip(paths, lists)}

    async def save(self, doc: DocumentRecord, ext: str, original: bytes | None, chunks: list[Chunk]) -> None:
        storage = self._require()
        base = f"{PREFIX}{doc.id}/"
        if original is not None:
            await storage.put(f"{base}original{ext}", original, mimetypes.types_map.get(ext, "application/octet-stream"))
        if chunks:
            await storage.put(f"{base}chunks.json", json.dumps([c.model_dump() for c in chunks]).encode(), "application/json")
        await storage.put(f"{base}meta.json", doc.model_dump_json().encode(), "application/json")

    async def original(self, doc_id: str) -> tuple[bytes, str] | None:
        snap = await self._snapshot()
        path = next((p for p in snap if p.startswith(f"{PREFIX}{doc_id}/original.")), None)
        if path is None:
            return None
        data = await self._require().get(path)
        return (data, path.rsplit(".", 1)[-1]) if data is not None else None

    async def delete(self, doc_id: str) -> bool:
        if await self.get(doc_id) is None:
            return False
        await self._require().delete_prefix(f"{PREFIX}{doc_id}/")
        return True


store = DocumentStore()
