"""Background ingestion pipeline: parse -> chunk -> index.

Each stage runs in a worker thread (asyncio.to_thread) so CPU-heavy PDF parsing
never blocks the event loop that is busy streaming chat tokens. A semaphore caps
how many documents are processed at once.
"""

import asyncio
import logging
import re

from pypdf import PdfReader

from .config import CHUNK_CHARS, CHUNK_OVERLAP, INGEST_CONCURRENCY
from .retrieval import index
from .schemas import Chunk
from .store import store

log = logging.getLogger(__name__)
_slots = asyncio.Semaphore(INGEST_CONCURRENCY)


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_pages(doc_id: str) -> list[str]:
    path = store.original_path(doc_id)
    if path is None:
        raise FileNotFoundError("Uploaded file is missing")
    if path.suffix.lower() != ".pdf":
        text = path.read_text(encoding="utf-8", errors="replace")
        # Treat ~3000 chars as a "page" so plain-text citations still have a locator.
        return [_clean(text[i : i + 3000]) for i in range(0, max(len(text), 1), 3000)]

    reader = PdfReader(str(path))
    total = len(reader.pages)
    pages = []
    for i, page in enumerate(reader.pages):
        pages.append(_clean(page.extract_text() or ""))
        if i % 5 == 0 or i == total - 1:
            store.update(doc_id, progress=0.05 + 0.65 * (i + 1) / total, detail=f"Parsed page {i + 1} of {total}")
    return pages


def _split(text: str) -> list[str]:
    """Split on paragraph/sentence boundaries into ~CHUNK_CHARS windows with overlap."""
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if cut > CHUNK_CHARS // 2:
                end = start + cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [c for c in out if c]


def _chunk(doc_id: str, filename: str, pages: list[str]) -> list[Chunk]:
    chunks = []
    for page_no, text in enumerate(pages, start=1):
        for j, piece in enumerate(_split(text)):
            chunks.append(Chunk(id=f"{doc_id}:{page_no}:{j}", doc_id=doc_id, filename=filename, page=page_no, text=piece))
    return chunks


async def ingest_document(doc_id: str) -> None:
    async with _slots:
        doc = store.get(doc_id)
        if doc is None:
            return  # deleted while queued
        try:
            store.update(doc_id, status="parsing", progress=0.05, detail="Extracting text")
            pages = await asyncio.to_thread(_extract_pages, doc_id)
            if not any(pages):
                raise ValueError("No extractable text (scanned PDF? OCR is not enabled)")

            store.update(doc_id, status="chunking", progress=0.75, detail="Splitting into passages", pages=len(pages))
            chunks = await asyncio.to_thread(_chunk, doc_id, doc.filename, pages)

            store.update(doc_id, status="indexing", progress=0.9, detail=f"Indexing {len(chunks)} passages")
            await asyncio.to_thread(store.set_chunks, doc_id, chunks)
            store.update(doc_id, status="ready", progress=1.0, detail="Ready", chunks=len(chunks))
            await asyncio.to_thread(index._ensure_fresh)  # warm the index off the request path
        except Exception as e:  # surface every failure on the document card
            log.exception("Ingestion failed for %s", doc_id)
            if store.get(doc_id):
                store.update(doc_id, status="failed", error=str(e), detail="Failed")
