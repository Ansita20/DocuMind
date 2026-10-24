import asyncio
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .agents import run_pipeline
from .config import ALLOWED_EXTENSIONS, CORS_ORIGINS, LLM_MODEL, MAX_UPLOAD_BYTES, MAX_UPLOAD_MB
from .ingestion import process
from .retrieval import index
from .schemas import ChatRequest, DocumentRecord, SearchRequest
from .store import StorageNotConfigured, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="DocuMind RAG API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StorageNotConfigured)
async def storage_missing(_: Request, exc: StorageNotConfigured) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "model": LLM_MODEL,
        "llm_configured": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        "storage": store.backend,
        "max_upload_mb": MAX_UPLOAD_MB,
    }


@app.get("/api/documents")
async def list_documents() -> list[DocumentRecord]:
    return await store.all()


@app.post("/api/documents", status_code=201)
async def upload_documents(files: list[UploadFile]) -> list[DocumentRecord]:
    """Parse, chunk and store each file within the request; the response carries the final status."""
    for f in files:
        if Path(f.filename or "").suffix.lower() not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, f"{f.filename}: only {', '.join(sorted(ALLOWED_EXTENSIONS))} are supported")

    created = []
    for f in files:
        ext = Path(f.filename).suffix.lower()
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        doc = DocumentRecord(
            id=uuid.uuid4().hex[:12], filename=Path(f.filename).name, size_bytes=len(data), created_at=time.time()
        )
        if len(data) > MAX_UPLOAD_BYTES:
            doc.status, doc.error, doc.detail = "failed", f"File exceeds {MAX_UPLOAD_MB:g} MB", "Failed"
            created.append(doc)  # not stored: nothing useful to keep
            continue
        try:
            pages, chunks = await asyncio.to_thread(process, doc.id, doc.filename, data, ext)
            doc = doc.model_copy(
                update={"status": "ready", "progress": 1.0, "detail": "Ready", "pages": pages, "chunks": len(chunks)}
            )
        except Exception as e:  # unreadable or empty files are recorded as failed documents
            logging.exception("Processing failed for %s", doc.filename)
            doc = doc.model_copy(update={"status": "failed", "error": str(e), "detail": "Failed"})
            chunks = []
        await store.save(doc, ext, data if doc.status == "ready" else None, chunks)
        created.append(doc)
    return created


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: str) -> DocumentRecord:
    doc = await store.get(doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


@app.get("/api/documents/{doc_id}/file")
async def get_document_file(doc_id: str) -> Response:
    doc, original = await store.get(doc_id), await store.original(doc_id)
    if doc is None or original is None:
        raise HTTPException(404, "Document not found")
    data, ext = original
    media = "application/pdf" if ext == "pdf" else "text/plain; charset=utf-8"
    safe_name = doc.filename.replace('"', "")
    return Response(data, media_type=media, headers={"Content-Disposition": f'inline; filename="{safe_name}"'})


@app.delete("/api/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str) -> None:
    if not await store.delete(doc_id):
        raise HTTPException(404, "Document not found")


@app.post("/api/search")
async def search(req: SearchRequest) -> list[dict]:
    """Inspect raw retrieval results without calling the LLM."""
    index.refresh(await store.ready_chunks())
    hits = index.search(req.query, k=req.k, doc_ids=set(req.doc_ids) if req.doc_ids is not None else None)
    return [{"score": round(s, 3), **c.model_dump()} for c, s in hits]


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        run_pipeline(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Production: serve the built React app (on Vercel it is promoted to the CDN at build time).
# In development Vite serves the frontend and proxies /api here, so the build may not exist.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.frontend("/", directory=FRONTEND_DIST)
