import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


LLM_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
# Vercel Functions reject request bodies over 4.5 MB, so the default cap is lower there.
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", 4.4 if os.getenv("VERCEL") else 50))
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)
CHUNK_CHARS = _int("CHUNK_CHARS", 1400)
CHUNK_OVERLAP = _int("CHUNK_OVERLAP", 250)
RETRIEVAL_TOP_K = _int("RETRIEVAL_TOP_K", 10)
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}

