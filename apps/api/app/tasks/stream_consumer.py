from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal
from uuid import UUID

import httpx
import orjson
import structlog

from app.config import Settings
from app.db import db
from app.generated.analysis_result import AnalysisResult
from app.services.analysis_partial import (
    apply_delta,
    buffers_to_partial,
    empty_buffers,
)

log = structlog.get_logger(__name__)

_wake = asyncio.Event()
_cancel_flags: dict[UUID, asyncio.Event] = {}

StreamOutcome = Literal["done", "cancelled", "retry", "broken"]


def wake_analysis_dispatcher() -> None:
    _wake.set()


async def analysis_dispatcher_loop(settings: Settings, stop: asyncio.Event) -> None:
    sem = asyncio.Semaphore(settings.analysis_concurrency)
    # Fail-closed recovery for interrupted streams
    await _mark_interrupted_streaming()

    workers: set[asyncio.Task] = set()

    while not stop.is_set():
        _wake.clear()
        claimed = await _claim_queued()
        for analysis_id, org_id, transcript in claimed:
            await sem.acquire()
            task = asyncio.create_task(
                _run_one(settings, analysis_id, org_id, transcript, sem)
            )
            workers.add(task)
            task.add_done_callback(workers.discard)

        try:
            await asyncio.wait_for(_wake.wait(), timeout=1.0)
        except TimeoutError:
            pass

    for t in list(workers):
        t.cancel()
    if workers:
        await asyncio.gather(*workers, return_exceptions=True)


async def _mark_interrupted_streaming() -> None:
    assert db.webhook_pool is not None
    async with db.webhook_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE analyses
            SET status = 'error',
                error = 'interrupted_restart',
                updated_at = now()
            WHERE status = 'streaming'
            """
        )


async def _claim_queued() -> list[tuple[UUID, UUID, str]]:
    assert db.webhook_pool is not None
    out: list[tuple[UUID, UUID, str]] = []
    async with db.webhook_pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT a.id, a.org_id, ca.transcript
                FROM analyses a
                JOIN call_attempts ca ON ca.id = a.call_attempt_id
                WHERE a.status = 'queued'
                ORDER BY a.created_at
                FOR UPDATE OF a SKIP LOCKED
                LIMIT 4
                """
            )
            for row in rows:
                await conn.execute(
                    """
                    UPDATE analyses
                    SET status = 'streaming', updated_at = now()
                    WHERE id = $1
                    """,
                    row["id"],
                )
                out.append((row["id"], row["org_id"], row["transcript"] or ""))
    return out


async def _run_one(
    settings: Settings,
    analysis_id: UUID,
    org_id: UUID,
    transcript: str,
    sem: asyncio.Semaphore,
) -> None:
    structlog.contextvars.bind_contextvars(analysis_id=str(analysis_id))
    cancel_event = asyncio.Event()
    _cancel_flags[analysis_id] = cancel_event
    try:
        await _consume_provider(settings, analysis_id, org_id, transcript, cancel_event)
    except Exception:  # noqa: BLE001
        log.exception("stream_consumer.fatal")
        await _set_error(analysis_id, "internal consumer error", keep_partial=True)
    finally:
        _cancel_flags.pop(analysis_id, None)
        sem.release()
        structlog.contextvars.unbind_contextvars("analysis_id")


async def _load_saved_buffers(
    analysis_id: UUID,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    buffers = empty_buffers()
    saved_chunks: list[tuple[str, str]] = []
    assert db.webhook_pool is not None
    async with db.webhook_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT field, delta FROM analysis_chunks
            WHERE analysis_id = $1 ORDER BY seq
            """,
            analysis_id,
        )
        for r in rows:
            apply_delta(buffers, r["field"], r["delta"])
            saved_chunks.append((r["field"], r["delta"]))
    return buffers, saved_chunks


async def _iter_sse_frames(
    resp: httpx.Response,
) -> AsyncIterator[tuple[str, str]]:
    event_name: str | None = None
    data_lines: list[str] = []
    async for line in resp.aiter_lines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
        elif line == "" and event_name and data_lines:
            yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
        elif line == "":
            event_name = None
            data_lines = []


async def _stream_once(
    client: httpx.AsyncClient,
    settings: Settings,
    analysis_id: UUID,
    transcript: str,
    buffers: dict[str, str],
    saved_chunks: list[tuple[str, str]],
    cancel_event: asyncio.Event,
    *,
    attempt: int,
) -> StreamOutcome:
    try:
        async with client.stream(
            "POST",
            f"{settings.provider_url.rstrip('/')}/v1/analyze",
            json={"request_id": str(analysis_id), "transcript": transcript},
        ) as resp:
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "1"))
                await asyncio.sleep(max(1, retry_after))
                return "retry"
            if resp.status_code != 200:
                return "broken"

            skip = len(saved_chunks)
            seen = 0
            async for event_name, data_raw in _iter_sse_frames(resp):
                if await _is_cancelled(analysis_id) or cancel_event.is_set():
                    await _set_cancelled(analysis_id)
                    return "cancelled"
                done = await _handle_provider_event(
                    analysis_id,
                    event_name,
                    data_raw,
                    buffers,
                    saved_chunks,
                    skip,
                    seen,
                )
                if event_name == "chunk":
                    seen += 1
                if done:
                    return "done"
            # Stream ended without done → retry with backoff
            log.warning("stream_consumer.broken", attempt=attempt)
            return "broken"
    except httpx.HTTPError:
        log.warning("stream_consumer.http_error", attempt=attempt, exc_info=True)
        return "broken"


async def _consume_provider(
    settings: Settings,
    analysis_id: UUID,
    org_id: UUID,
    transcript: str,
    cancel_event: asyncio.Event,
) -> None:
    # org_id kept for call-site symmetry with claim/dispatcher; provider URL is global.
    _ = org_id
    buffers, saved_chunks = await _load_saved_buffers(analysis_id)
    max_attempts = 5
    attempt = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
        while attempt < max_attempts:
            if await _is_cancelled(analysis_id) or cancel_event.is_set():
                await _set_cancelled(analysis_id)
                return

            attempt += 1
            outcome = await _stream_once(
                client,
                settings,
                analysis_id,
                transcript,
                buffers,
                saved_chunks,
                cancel_event,
                attempt=attempt,
            )
            if outcome in ("done", "cancelled"):
                return
            if outcome == "retry":
                continue
            await asyncio.sleep(min(8, 2**attempt))

    await _set_error(
        analysis_id, "provider stream broken, retries exhausted", keep_partial=True
    )


async def _persist_chunk(
    analysis_id: UUID,
    buffers: dict[str, str],
    saved_chunks: list[tuple[str, str]],
    field: str,
    delta: str,
) -> None:
    apply_delta(buffers, field, delta)
    saved_chunks.append((field, delta))
    seq = len(saved_chunks)
    partial = buffers_to_partial(buffers)
    assert db.webhook_pool is not None
    async with db.webhook_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO analysis_chunks (analysis_id, seq, field, delta)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (analysis_id, seq) DO NOTHING
                """,
                analysis_id,
                seq,
                field,
                delta,
            )
            await conn.execute(
                """
                UPDATE analyses
                SET partial = $2::jsonb, updated_at = now()
                WHERE id = $1 AND status = 'streaming'
                """,
                analysis_id,
                orjson.dumps(partial).decode(),
            )


async def _complete_analysis(
    analysis_id: UUID,
    buffers: dict[str, str],
    result: object,
) -> None:
    try:
        validated = AnalysisResult.model_validate(result)
        assert db.webhook_pool is not None
        async with db.webhook_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE analyses
                SET status = 'done',
                    result = $2::jsonb,
                    partial = $3::jsonb,
                    updated_at = now()
                WHERE id = $1 AND status = 'streaming'
                """,
                analysis_id,
                validated.model_dump_json(),
                orjson.dumps(buffers_to_partial(buffers)).decode(),
            )
    except Exception as exc:  # noqa: BLE001
        await _set_error(
            analysis_id,
            f"invalid provider result: {exc}",
            keep_partial=True,
        )


async def _handle_provider_event(
    analysis_id: UUID,
    event_name: str,
    data_raw: str,
    buffers: dict[str, str],
    saved_chunks: list[tuple[str, str]],
    skip: int,
    seen: int,
) -> bool:
    """Returns True if analysis reached terminal state."""
    try:
        data = orjson.loads(data_raw)
    except orjson.JSONDecodeError:
        return False

    if event_name == "chunk":
        field = data.get("field")
        delta = data.get("delta")
        if not isinstance(field, str) or not isinstance(delta, str):
            return False
        if seen < skip:
            exp_field, exp_delta = saved_chunks[seen]
            if field != exp_field or delta != exp_delta:
                await _set_error(
                    analysis_id,
                    "provider stream prefix mismatch on resume",
                    keep_partial=True,
                )
                return True
            return False
        await _persist_chunk(analysis_id, buffers, saved_chunks, field, delta)
        return False

    if event_name == "done":
        await _complete_analysis(analysis_id, buffers, data.get("result"))
        return True

    return False


async def _is_cancelled(analysis_id: UUID) -> bool:
    assert db.webhook_pool is not None
    async with db.webhook_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, cancel_requested FROM analyses WHERE id = $1",
            analysis_id,
        )
    if row is None:
        return True
    return bool(row["cancel_requested"]) or row["status"] == "cancelled"


async def _set_cancelled(analysis_id: UUID) -> None:
    assert db.webhook_pool is not None
    async with db.webhook_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE analyses
            SET status = 'cancelled', cancel_requested = true, updated_at = now()
            WHERE id = $1 AND status IN ('queued', 'streaming')
            """,
            analysis_id,
        )


async def _set_error(analysis_id: UUID, message: str, *, keep_partial: bool) -> None:
    # keep_partial documents caller intent; SQL leaves existing partial untouched.
    _ = keep_partial
    assert db.webhook_pool is not None
    async with db.webhook_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE analyses
            SET status = 'error', error = $2, updated_at = now()
            WHERE id = $1 AND status IN ('queued', 'streaming')
            """,
            analysis_id,
            message,
        )
