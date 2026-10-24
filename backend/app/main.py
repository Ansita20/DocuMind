import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .agents import run_pipeline
from .config import ALLOWED_EXTENSIONS, LLM_MODEL, CORS_ORIGINS, MAX_UPLOAD_BYTES
from .ingestion import ingest_document
from .retrieval import index
from .schemas import ChatRequest, DocumentRecord, SearchRequest
from .store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="DocuMind RAG API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "model": LLM_MODEL,
        "llm_configured": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        "documents": len(store.all()),
    }


@app.get("/api/documents")
def list_documents() -> list[DocumentRecord]:
    return store.all()


@app.post("/api/documents", status_code=202)
async def upload_documents(files: list[UploadFile], background: BackgroundTasks) -> list[DocumentRecord]:
    """Accept files immediately and hand them to the background ingestion pipeline."""
    for f in files:
        if Path(f.filename or "").suffix.lower() not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, f"{f.filename}: only {', '.join(sorted(ALLOWED_EXTENSIONS))} are supported")

    created = []
    for f in files:
        ext = Path(f.filename).suffix.lower()
        doc_id = uuid.uuid4().hex[:12]
        dest = store.doc_dir(doc_id)
        dest.mkdir(parents=True, exist_ok=True)
        size, too_big = 0, False
        with open(dest / f"original{ext}", "wb") as out:
            while chunk := await f.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    too_big = True
                    break
                out.write(chunk)
        doc = DocumentRecord(id=doc_id, filename=Path(f.filename).name, size_bytes=size, created_at=time.time())
        if too_big:
            limit = MAX_UPLOAD_BYTES // 1024 // 1024
            doc = doc.model_copy(update={"status": "failed", "error": f"File exceeds {limit} MB", "detail": "Failed"})
            store.add(doc)
            (dest / f"original{ext}").unlink(missing_ok=True)
        else:
            store.add(doc)
            background.add_task(ingest_document, doc_id)
        created.append(doc)
    return created


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str) -> DocumentRecord:
    doc = store.get(doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


@app.get("/api/documents/{doc_id}/file")
def get_document_file(doc_id: str) -> FileResponse:
    doc, path = store.get(doc_id), store.original_path(doc_id)
    if doc is None or path is None:
        raise HTTPException(404, "Document not found")
    media = "application/pdf" if path.suffix == ".pdf" else "text/plain; charset=utf-8"
    return FileResponse(path, media_type=media, filename=doc.filename, content_disposition_type="inline")


@app.delete("/api/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str) -> None:
    if not store.delete(doc_id):
        raise HTTPException(404, "Document not found")


@app.post("/api/search")
def search(req: SearchRequest) -> list[dict]:
    """Inspect raw retrieval results without calling the LLM."""
    hits = index.search(req.query, k=req.k, doc_ids=set(req.doc_ids) if req.doc_ids is not None else None)
    return [{"score": round(s, 3), **c.model_dump()} for c, s in hits]


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        run_pipeline(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
