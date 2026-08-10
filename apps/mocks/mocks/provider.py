"""LLM provider mock implementing POST /v1/analyze (spec/provider.openapi.yaml)."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, AsyncIterator

from fastapi import APIRouter
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter()

CHUNK_DELAY_S = 0.05

HAPPY_RESULT: dict[str, Any] = {
    "summary": "Клиент интересовался тарифами",
    "objections": ["дорого"],
    "next_step": "send_proposal",
    "lead_score": 62,
    "confidence": 0.81,
}

# Fixed sequence — identical for every happy-path request_id (deterministic).
HAPPY_CHUNKS: list[tuple[str, str]] = [
    ("summary", "Клиент "),
    ("summary", "интересовался "),
    ("summary", "тарифами"),
    ("objections", "["),
    ("objections", '"дорого"'),
    ("objections", "]"),
    ("next_step", "send_"),
    ("next_step", "proposal"),
    ("lead_score", "6"),
    ("lead_score", "2"),
    ("confidence", "0."),
    ("confidence", "81"),
]

# Missing required fields / wrong types — consumer must reject.
INVALID_RESULT: dict[str, Any] = {
    "summary": "broken",
    "objections": "not-an-array",
    "lead_score": "high",
}


class AnalyzeRequest(BaseModel):
    request_id: str = Field(min_length=1)
    transcript: str = Field(min_length=1)


class _AnalyzeState:
    """In-memory counters and one-shot chaos flags (single-process mock)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.total = 0
        self.by_request_id: dict[str, int] = defaultdict(int)
        self.chaos_429_seen: set[str] = set()
        self.chaos_break_seen: set[str] = set()

    async def record_call(self, request_id: str) -> None:
        async with self._lock:
            self.total += 1
            self.by_request_id[request_id] += 1

    async def consume_429(self, request_id: str) -> bool:
        """Return True if this call should get 429 (first time only)."""
        async with self._lock:
            if request_id in self.chaos_429_seen:
                return False
            self.chaos_429_seen.add(request_id)
            return True

    async def consume_break(self, request_id: str) -> bool:
        """Return True if this call should break the stream (first time only)."""
        async with self._lock:
            if request_id in self.chaos_break_seen:
                return False
            self.chaos_break_seen.add(request_id)
            return True

    async def debug_snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "count": self.total,
                "by_request_id": dict(self.by_request_id),
            }


state = _AnalyzeState()


def _sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


async def _stream(
    chunks: list[tuple[str, str]],
    *,
    done_result: dict[str, Any] | None,
) -> AsyncIterator[str]:
    for field, delta in chunks:
        yield _sse("chunk", {"field": field, "delta": delta})
        await asyncio.sleep(CHUNK_DELAY_S)
    if done_result is not None:
        yield _sse("done", {"result": done_result})


def _sse_response(
    chunks: list[tuple[str, str]],
    *,
    done_result: dict[str, Any] | None,
) -> StreamingResponse:
    return StreamingResponse(
        _stream(chunks, done_result=done_result),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/v1/analyze")
async def analyze(body: AnalyzeRequest) -> Response:
    await state.record_call(body.request_id)
    transcript = body.transcript
    request_id = body.request_id

    if "CHAOS_429" in transcript and await state.consume_429(request_id):
        return Response(status_code=429, headers={"Retry-After": "1"})

    if "CHAOS_BREAK" in transcript and await state.consume_break(request_id):
        halfway = max(1, len(HAPPY_CHUNKS) // 2)
        return _sse_response(HAPPY_CHUNKS[:halfway], done_result=None)

    if "CHAOS_INVALID" in transcript:
        # A few valid-looking chunks, then a schema-invalid terminal result.
        return _sse_response(HAPPY_CHUNKS[:3], done_result=INVALID_RESULT)

    return _sse_response(HAPPY_CHUNKS, done_result=HAPPY_RESULT)


@router.get("/_debug/analyze_calls")
async def analyze_calls() -> dict[str, Any]:
    return await state.debug_snapshot()
