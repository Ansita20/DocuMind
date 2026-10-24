import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


LLM_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
MAX_UPLOAD_BYTES = _int("MAX_UPLOAD_MB", 50) * 1024 * 1024
CHUNK_CHARS = _int("CHUNK_CHARS", 1400)
CHUNK_OVERLAP = _int("CHUNK_OVERLAP", 250)
RETRIEVAL_TOP_K = _int("RETRIEVAL_TOP_K", 10)
INGEST_CONCURRENCY = _int("INGEST_CONCURRENCY", 2)
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}

DATA_DIR.mkdir(parents=True, exist_ok=True)
