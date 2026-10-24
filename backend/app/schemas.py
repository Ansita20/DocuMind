from typing import Literal

from pydantic import BaseModel, Field

DocStatus = Literal["queued", "parsing", "chunking", "indexing", "ready", "failed"]


class DocumentRecord(BaseModel):
    id: str
    filename: str
    size_bytes: int
    status: DocStatus = "queued"
    progress: float = 0.0  # 0..1
    detail: str = "Waiting in queue"
    pages: int = 0
    chunks: int = 0
    error: str | None = None
    created_at: float


class Chunk(BaseModel):
    id: str
    doc_id: str
    filename: str
    page: int  # 1-indexed
    text: str


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1)
    doc_ids: list[str] | None = None  # None = search every ready document


class SearchRequest(BaseModel):
    query: str
    doc_ids: list[str] | None = None
    k: int = 8
