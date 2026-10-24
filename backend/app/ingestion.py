"""Ingestion pipeline: parse -> chunk.

Runs inside the upload request (serverless functions stop once a response is
sent, so there is no background work). Parsing is CPU-bound, so it runs in a
worker thread to keep the event loop free for concurrent chat streams.
"""

import io
import re

from pypdf import PdfReader

from .config import CHUNK_CHARS, CHUNK_OVERLAP
from .schemas import Chunk


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pages(data: bytes, ext: str) -> list[str]:
    if ext != ".pdf":
        text = data.decode("utf-8", errors="replace")
        # Treat ~3000 chars as a "page" so plain-text citations still have a locator.
        return [_clean(text[i : i + 3000]) for i in range(0, max(len(text), 1), 3000)]
    return [_clean(page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages]


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


def process(doc_id: str, filename: str, data: bytes, ext: str) -> tuple[int, list[Chunk]]:
    """Return (page count, passages). Raises ValueError when there is no extractable text."""
    pages = extract_pages(data, ext)
    if not any(pages):
        raise ValueError("No extractable text (scanned PDF? OCR is not enabled)")
    chunks = [
        Chunk(id=f"{doc_id}:{page_no}:{j}", doc_id=doc_id, filename=filename, page=page_no, text=piece)
        for page_no, text in enumerate(pages, start=1)
        for j, piece in enumerate(_split(text))
    ]
    return len(pages), chunks
