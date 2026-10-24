"""Multi-agent RAG orchestration on Google Gemini, streamed as Server-Sent Events.

    Planner agent  ->  Retriever  ->  Analyst agent
    (rewrites the     (BM25 + RRF    (answers from numbered passages,
     question into     over chunks)   citing them inline as [n],
     search queries)                  streamed token by token)

Each stage emits SSE events so the UI can show the pipeline live.
"""

import json
import logging
import os
from collections.abc import AsyncIterator

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from .config import LLM_MODEL, RETRIEVAL_TOP_K
from .retrieval import index, multi_query_search
from .schemas import ChatRequest, ChatTurn, Chunk
from .store import StorageNotConfigured, store

log = logging.getLogger(__name__)

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
            raise ValueError("No Gemini API key found. Set GEMINI_API_KEY in backend/.env.")
        # Reads GEMINI_API_KEY / GOOGLE_API_KEY. Retries ride out transient
        # overload (503 "high demand") and rate limits with exponential backoff.
        _client = genai.Client(
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(
                    attempts=4, initial_delay=1.0, max_delay=10.0, http_status_codes=[429, 500, 503, 504]
                )
            )
        )
    return _client


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _contents(turns: list[ChatTurn]) -> list[types.Content]:
    return [
        types.Content(role="model" if t.role == "assistant" else "user", parts=[types.Part(text=t.content)])
        for t in turns
    ]


# --------------------------------------------------------------------------- #
# Planner agent
# --------------------------------------------------------------------------- #

PLANNER_SYSTEM = """You plan document searches for a corporate document analyst.

Given a conversation and the user's latest message, decide whether answering it needs \
the uploaded documents, and if so write 1 to 4 standalone keyword search queries that \
together cover everything the answer needs. Resolve pronouns and follow-up references \
using the conversation ("what about last year?" -> "revenue fiscal 2023"). Include \
likely synonyms used in filings (e.g. "net sales" for revenue, "operating income" for EBIT). \
For multi-part or comparative questions, write one query per part.

In `focus`, state in one sentence what the final answer must establish."""


class QueryPlan(BaseModel):
    needs_retrieval: bool
    sub_queries: list[str]
    focus: str


def _transcript(turns: list[ChatTurn]) -> str:
    return "\n\n".join(f"{t.role.upper()}: {t.content}" for t in turns[-8:])


async def plan(turns: list[ChatTurn], filenames: list[str]) -> dict:
    question = turns[-1].content
    default = {"needs_retrieval": True, "sub_queries": [question], "focus": question}
    prompt = (
        f"Available documents: {', '.join(filenames) or '(none uploaded)'}\n\n"
        f"<conversation>\n{_transcript(turns)}\n</conversation>"
    )
    resp = await client().aio.models.generate_content(
        model=LLM_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=PLANNER_SYSTEM,
            response_mime_type="application/json",
            response_schema=QueryPlan,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        ),
    )
    parsed = resp.parsed
    if not isinstance(parsed, QueryPlan):
        log.warning("Planner returned no valid plan; using the raw question")
        return default
    result = parsed.model_dump()
    queries = [q.strip() for q in result["sub_queries"] if q.strip()][:4]
    result["sub_queries"] = queries or [question]
    return result


# --------------------------------------------------------------------------- #
# Analyst agent
# --------------------------------------------------------------------------- #

ANALYST_SYSTEM = """You are a senior financial and corporate document analyst. You answer \
questions using the numbered document passages provided in the user's message.

Citations:
- Cite the passage(s) supporting each factual claim immediately after the claim, using \
their numbers in square brackets, e.g. "Revenue grew 18% [2]." or "... [1][4]."
- Only cite passage numbers that were provided. Never cite a passage that doesn't support the claim.

Answering:
- Ground every factual claim in the passages; never invent figures.
- When the passages don't contain what's needed, say exactly what is missing rather than guessing.
- Show calculations (growth rates, margins, ratios) explicitly, with the inputs you used. Write \
them in plain text (e.g. "$221M − $64M = $157M"), never LaTeX.
- Use markdown: short paragraphs, bullet points, and tables for multi-period or multi-entity numbers.
- Lead with the direct answer, then the supporting detail.
- If no passages are provided, answer briefly from general knowledge, say that no uploaded \
document supported the answer, and include no citations."""


def _source_payload(i: int, c: Chunk) -> dict:
    return {"index": i, "chunk_id": c.id, "doc_id": c.doc_id, "filename": c.filename, "page": c.page, "text": c.text}


def _analyst_contents(turns: list[ChatTurn], chunks: list[Chunk], focus: str) -> list[types.Content]:
    passages = "\n\n".join(
        f'<passage id="{i + 1}" file="{c.filename}" page="{c.page}">\n{c.text}\n</passage>'
        for i, c in enumerate(chunks)
    )
    question = turns[-1].content
    if focus and focus != question:
        question += f"\n\n(Analysis focus: {focus})"
    final = f"<passages>\n{passages or '(none)'}\n</passages>\n\n{question}"
    return _contents(turns[:-1]) + [types.Content(role="user", parts=[types.Part(text=final)])]


def _normalize(turns: list[ChatTurn]) -> list[ChatTurn]:
    """Drop empty turns and merge consecutive same-role turns; must start and end with user."""
    out: list[ChatTurn] = []
    for t in turns:
        if not t.content.strip():
            continue
        if out and out[-1].role == t.role:
            out[-1] = ChatTurn(role=t.role, content=out[-1].content + "\n\n" + t.content)
        else:
            out.append(t)
    while out and out[0].role != "user":
        out.pop(0)
    if not out or out[-1].role != "user":
        raise ValueError("The last message must be from the user")
    return out


BLOCKED = {
    types.FinishReason.SAFETY,
    types.FinishReason.RECITATION,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.SPII,
}


async def run_pipeline(req: ChatRequest) -> AsyncIterator[str]:
    try:
        turns = _normalize(req.messages)
        ready = [d for d in await store.all() if d.status == "ready"]
        if req.doc_ids is not None:
            ready = [d for d in ready if d.id in set(req.doc_ids)]
        scope = {d.id for d in ready}

        # 1. Planner agent
        yield sse("stage", {"id": "plan", "status": "active", "detail": "Planning search strategy"})
        the_plan = await plan(turns, [d.filename for d in ready])
        yield sse("plan", the_plan)
        yield sse("stage", {"id": "plan", "status": "done", "detail": f"{len(the_plan['sub_queries'])} search queries"})

        # 2. Retriever
        chunks: list[Chunk] = []
        if the_plan["needs_retrieval"] and scope:
            yield sse("stage", {"id": "retrieve", "status": "active", "detail": f"Searching {len(scope)} document(s)"})
            index.refresh(await store.ready_chunks())
            chunks = multi_query_search(the_plan["sub_queries"], k=RETRIEVAL_TOP_K, doc_ids=scope)
            detail = f"{len(chunks)} passages from {len({c.doc_id for c in chunks})} document(s)"
        else:
            detail = "Skipped (no documents needed)" if scope else "Skipped (no ready documents)"
        yield sse("sources", {"sources": [_source_payload(i, c) for i, c in enumerate(chunks)]})
        yield sse("stage", {"id": "retrieve", "status": "done", "detail": detail})

        # 3. Analyst agent, streamed
        yield sse("stage", {"id": "answer", "status": "active", "detail": "Analyzing"})
        stream = await client().aio.models.generate_content_stream(
            model=LLM_MODEL,
            contents=_analyst_contents(turns, chunks, the_plan.get("focus", "")),
            config=types.GenerateContentConfig(
                system_instruction=ANALYST_SYSTEM,
                # Model-default thinking depth; raise to ThinkingLevel.HIGH for harder analyses at higher latency.
                thinking_config=types.ThinkingConfig(include_thoughts=True),
            ),
        )
        finish, usage, model = None, None, LLM_MODEL
        async for chunk in stream:
            usage = chunk.usage_metadata or usage
            model = chunk.model_version or model
            if chunk.prompt_feedback and chunk.prompt_feedback.block_reason:
                yield sse("error", {"message": f"Request blocked by Gemini ({chunk.prompt_feedback.block_reason})."})
                return
            if not chunk.candidates:
                continue
            cand = chunk.candidates[0]
            for part in (cand.content.parts if cand.content else None) or []:
                if not part.text:
                    continue
                yield sse("thinking" if part.thought else "token", {"text": part.text})
            finish = cand.finish_reason or finish

        if finish in BLOCKED:
            yield sse("error", {"message": f"Gemini stopped the answer ({finish.name})."})
            return
        if finish == types.FinishReason.MAX_TOKENS:
            yield sse("notice", {"message": "Answer was truncated at the output limit."})
        yield sse("stage", {"id": "answer", "status": "done", "detail": "Complete"})
        yield sse("done", {
            "model": model,
            "input_tokens": (usage.prompt_token_count or 0) if usage else 0,
            "output_tokens": (usage.candidates_token_count or 0) if usage else 0,
        })

    except (ValueError, StorageNotConfigured) as e:  # bad history, missing API key or storage
        yield sse("error", {"message": str(e)})
    except errors.ClientError as e:
        log.warning("Gemini client error: %s", e)
        if e.code in (401, 403) or "API key" in str(e.message):
            msg = "Gemini rejected the API key. Check GEMINI_API_KEY in backend/.env."
        elif e.code == 429:
            msg = "Rate limited by the Gemini API. Wait a moment and retry."
        elif e.code == 404:
            msg = f"Model '{LLM_MODEL}' not found. Set GEMINI_MODEL in backend/.env to an available model."
        else:
            msg = f"Gemini API error ({e.code}): {e.message}"
        yield sse("error", {"message": msg})
    except errors.ServerError as e:
        log.exception("Gemini server error")
        yield sse("error", {"message": f"Gemini is unavailable ({e.code}). Retry shortly."})
    except Exception as e:
        log.exception("Pipeline failed")
        yield sse("error", {"message": f"Unexpected error: {e}"})
