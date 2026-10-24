# DocuMind — Multi-Agent RAG Document Analyst

Upload PDFs (annual reports, 10-Ks, earnings decks) and ask questions about them. Answers stream in token by token, with page-level citations you can click to see the exact passage the answer came from.

**Stack:** React 19 + Vite + TypeScript · FastAPI · Google Gemini (`gemini-3.8-flash`) via the official `google-genai` SDK.

## Architecture

```
React UI ──POST /api/documents──▶ FastAPI ──▶ parse (pypdf, worker thread) → chunk → store
   │        (one file per request)    │          storage: Vercel Blob in production, local disk in dev
   │
   └──POST /api/chat (SSE stream)──▶ Planner agent   rewrites question → 1–4 search queries (structured JSON output)
                                     Retriever       BM25 per query + reciprocal-rank fusion
                                     Analyst agent   streams the answer, citing passages inline as [n]
```

- **Serverless-ready ingestion.** Each upload is parsed and indexed within its own request, since serverless functions stop after responding. Parsing runs in a worker thread (`asyncio.to_thread`) so it never stalls concurrent chat streams.
- **Stateless storage.** Originals, metadata and passages live in Vercel Blob (local disk in development). Objects are written once and never overwritten, and reads are cached per object version, so warm instances only fetch what changed. The BM25 index is rebuilt in memory when the document set changes.
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

## Deploy to Vercel

The repo deploys as one Vercel project: FastAPI is the function, and the React build is served from the CDN through `app.frontend()`. The config lives in [`pyproject.toml`](pyproject.toml) (entrypoint and build command) and [`vercel.json`](vercel.json) (300 s max duration for streamed answers).

1. Import the GitHub repo in Vercel. Leave the framework preset on **FastAPI** and the root directory as the repo root.
2. **Storage → Create → Blob**: create a **private** store and connect it to the project. This sets the credentials automatically.
3. **Settings → Environment Variables**: add `GEMINI_API_KEY`.
4. Redeploy.

Limits on Vercel: about 4.4 MB per uploaded file (Vercel caps request bodies at 4.5 MB), and scanned PDFs need OCR, which isn't included.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Model, whether an API key is set, storage backend, upload limit |
| `GET` | `/api/documents` | List documents with ingestion status and progress |
| `POST` | `/api/documents` | Multipart upload (`files`); parses and indexes, returns the final status |
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
| `DATA_DIR` | `backend/data` | Local storage when no Blob store is configured |
| `MAX_UPLOAD_MB` | `50` (`4.4` on Vercel) | |
| `BLOB_ACCESS` | `private` | Must match the Blob store's access mode |
| `CHUNK_CHARS` / `CHUNK_OVERLAP` | `1400` / `250` | |
| `RETRIEVAL_TOP_K` | `10` | Passages sent to the analyst |

## Design notes and next steps

- **Retrieval is lexical (BM25)**, with no embedding model or vector database to run. That suits financial text, where exact terms like "EBITDA" or "FY2024" matter, and the planner adds synonyms to cover phrasing differences. To add semantic search, put an embedding model and vector store (pgvector, Qdrant) behind `multi_query_search` in [`backend/app/retrieval.py`](backend/app/retrieval.py) and fuse its ranking with BM25 through the same RRF step.
- **Scanned PDFs** have no text layer and fail with a clear error. Add OCR (e.g. Tesseract) to `_extract_pages` in [`backend/app/ingestion.py`](backend/app/ingestion.py), or send the PDF directly to Gemini, which reads PDFs natively.
- **Larger files** need uploads that bypass the function: Blob client uploads straight from the browser (`@vercel/blob/client`), with processing moved to a queue worker (Vercel Queues with Celery or Dramatiq).
- **Many documents:** the index is rebuilt from all passages on each instance. Past a few thousand passages, move retrieval to Postgres full-text search or a vector database.
- The planner runs at thinking level `LOW` and the analyst at the model default. Raise the analyst to `ThinkingLevel.HIGH` in [`backend/app/agents.py`](backend/app/agents.py) for harder analyses, at higher latency.
- To test the UI against a second backend, set `API_URL` for the dev proxy: `API_URL=http://localhost:8001 npm run dev`.
