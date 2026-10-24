"""Lexical retrieval with Okapi BM25 plus reciprocal-rank fusion across sub-queries.

BM25 needs no embedding model or vector database, and it is strong on financial
text where exact terms ("EBITDA", "FY2024", "$4.2B") matter. The index is rebuilt
whenever the set of ready documents changes; on a warm serverless instance it
is reused across requests.
"""

import math
import re
import threading
from collections import Counter

from .schemas import Chunk

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*%?")
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with "
    "what which who how did does do their they we our you your than then there these those been into".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._lock = threading.Lock()
        self._signature: tuple = ()
        self._chunks: list[Chunk] = []
        self._tfs: list[Counter] = []
        self._lens: list[int] = []
        self._idf: dict[str, float] = {}
        self._avgdl = 0.0

    def refresh(self, by_doc: dict[str, list[Chunk]]) -> None:
        signature = tuple(sorted((d, len(c)) for d, c in by_doc.items()))
        with self._lock:
            if signature == self._signature:
                return
            chunks = [c for doc_chunks in by_doc.values() for c in doc_chunks]
            tfs = [Counter(tokenize(c.text)) for c in chunks]
            df: Counter = Counter()
            for tf in tfs:
                df.update(tf.keys())
            n = len(chunks)
            self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
            self._chunks, self._tfs = chunks, tfs
            self._lens = [sum(tf.values()) for tf in tfs]
            self._avgdl = (sum(self._lens) / n) if n else 0.0
            self._signature = signature

    def search(self, query: str, k: int, doc_ids: set[str] | None = None) -> list[tuple[Chunk, float]]:
        terms = tokenize(query)
        if not terms:
            return []
        scored = []
        for i, (chunk, tf) in enumerate(zip(self._chunks, self._tfs)):
            if doc_ids is not None and chunk.doc_id not in doc_ids:
                continue
            norm = self.k1 * (1 - self.b + self.b * self._lens[i] / (self._avgdl or 1))
            score = 0.0
            for t in terms:
                f = tf.get(t)
                if f:
                    score += self._idf[t] * f * (self.k1 + 1) / (f + norm)
            if score > 0:
                scored.append((chunk, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]


index = BM25Index()


def multi_query_search(queries: list[str], k: int, doc_ids: set[str] | None = None) -> list[Chunk]:
    """Run each sub-query and merge the rankings with reciprocal-rank fusion."""
    fused: dict[str, float] = {}
    by_id: dict[str, Chunk] = {}
    for q in queries:
        for rank, (chunk, _) in enumerate(index.search(q, k=k * 2, doc_ids=doc_ids)):
            fused[chunk.id] = fused.get(chunk.id, 0.0) + 1.0 / (60 + rank)
            by_id[chunk.id] = chunk
    top = sorted(fused, key=fused.get, reverse=True)[:k]
    # Present excerpts in reading order so the model sees coherent context.
    return sorted((by_id[i] for i in top), key=lambda c: (c.filename, c.page, c.id))
