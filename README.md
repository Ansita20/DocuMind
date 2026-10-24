# DocuMind — Multi-Agent RAG Document Analyst

Upload PDFs (annual reports, 10-Ks, earnings decks) and ask questions about them. Answers stream in token by token, with page-level citations you can click to see the exact passage the answer came from.

**Stack:** React 19 + Vite + TypeScript · FastAPI · Google Gemini (`gemini-3.8-flash`) via the official `google-genai` SDK.

## Architecture

```
React UI ──POST /api/documents──▶ FastAPI ──BackgroundTasks──▶ ingestion pipeline
   │                                 │                          parse (pypdf, worker thread)
   │  polls status/progress  ◀───────┤                          → chunk (per page, overlapping)
   │                                 │                          → index (BM25)
   │
   └──POST /api/chat (SSE stream)──▶ Planner agent   rewrites question → 1–4 search queries (structured JSON output)
                                     Retriever       BM25 per query + reciprocal-rank fusion
                                     Analyst agent   streams the answer, citing passages inline as [n]
```

- **Non-blocking ingestion.** Uploads return `202` immediately. Parsing runs in worker threads (`asyncio.to_thread`) under a concurrency semaphore, so large PDFs never stall the event loop that is streaming chat tokens.
- **Streaming over SSE.** `/api/chat` emits typed events: `stage`, `plan`, `sources`, `thinking`, `token`, `notice`, `error`, `done`. The frontend reads them from the `fetch` body stream, since `EventSource` can't send a POST.
- **Grounded citations.** Retrieved passages are numbered in the prompt and the analyst cites them inline as `[n]`. The UI turns valid markers into clickable chips linked to the passage, file and page; numbers that match no passage stay plain text.
- **Resilience.** The Gemini client retries 429/5xx responses (e.g. 503 "high demand") with exponential backoff. Safety blocks and truncation surface as clear messages in the UI.

## Run it

**Backend** (Python 3.10+)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then set GEMINI_API_KEY
uvicorn app.main:app --reload --port 8000
```

**Frontend** (Node 20+)

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173 (proxies /api → :8000)
```

To try it, upload `samples/northwind_fy2024_annual_report.pdf`, a short fictional annual report, and click a suggested question.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Model name and whether an API key is configured |
| `GET` | `/api/documents` | List documents with ingestion status and progress |
| `POST` | `/api/documents` | Multipart upload (`files`); returns 202 and ingests in the background |
| `GET` | `/api/documents/{id}/file` | Original file (append `#page=N` in a browser) |
| `DELETE` | `/api/documents/{id}` | Remove a document and its index entries |
| `POST` | `/api/search` | Raw retrieval results, no LLM call (for debugging) |
| `POST` | `/api/chat` | `{messages, doc_ids?}` → SSE stream |

Interactive docs are at http://localhost:8000/docs.

## Configuration (`backend/.env`)

| Variable | Default | |
|---|---|---|
| `GEMINI_API_KEY` | — | Required for chat ([get one](https://aistudio.google.com/apikey)) |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Any text model your key can use, e.g. `gemini-3.1-pro-preview` for harder analyses |
| `DATA_DIR` | `backend/data` | Uploaded files, chunks, metadata |
| `MAX_UPLOAD_MB` | `50` | |
| `CHUNK_CHARS` / `CHUNK_OVERLAP` | `1400` / `250` | |
| `RETRIEVAL_TOP_K` | `10` | Passages sent to the analyst |
| `INGEST_CONCURRENCY` | `2` | Documents parsed in parallel |

## Design notes and next steps

- **Retrieval is lexical (BM25)**, with no embedding model or vector database to run. That suits financial text, where exact terms like "EBITDA" or "FY2024" matter, and the planner adds synonyms to cover phrasing differences. To add semantic search, put an embedding model and vector store (pgvector, Qdrant) behind `multi_query_search` in [`backend/app/retrieval.py`](backend/app/retrieval.py) and fuse its ranking with BM25 through the same RRF step.
- **Scanned PDFs** have no text layer and fail with a clear error. Add OCR (e.g. Tesseract) to `_extract_pages` in [`backend/app/ingestion.py`](backend/app/ingestion.py), or send the PDF directly to Gemini, which reads PDFs natively.
- **State is file-based** under `DATA_DIR` and there is a single process. For multiple workers, move the registry to Postgres and ingestion to a real queue (Celery, Arq, or RQ).
- The planner runs at thinking level `LOW` and the analyst at the model default. Raise the analyst to `ThinkingLevel.HIGH` in [`backend/app/agents.py`](backend/app/agents.py) for harder analyses, at higher latency.
- To test the UI against a second backend, set `API_URL` for the dev proxy: `API_URL=http://localhost:8001 npm run dev`.
