from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any
from uuid import UUID

import asyncpg
import orjson
import structlog

from app.config import get_settings
from app.db import db

log = structlog.get_logger(__name__)

TERMINAL = frozenset({"done", "error", "cancelled"})


def _sse(event: str, data: Any, event_id: int | None = None) -> str:
    payload = data if isinstance(data, str) else orjson.dumps(data).decode()
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    lines.append("")
    return "\n".join(lines) + "\n"


async def _fetch_snapshot(
    org_id: UUID,
    analysis_id: UUID,
    after_seq: int,
) -> tuple[asyncpg.Record | None, list[asyncpg.Record]]:
    async with db.tenant_connection(org_id) as conn:
        status_row = await conn.fetchrow(
            "SELECT status, result, error, partial FROM analyses WHERE id = $1",
            analysis_id,
        )
        chunks = await conn.fetch(
            """
            SELECT seq, field, delta
            FROM analysis_chunks
            WHERE analysis_id = $1 AND seq > $2
            ORDER BY seq
            """,
            analysis_id,
            after_seq,
        )
    return status_row, list(chunks)


def _emit_chunks(chunks: Sequence[asyncpg.Record]) -> tuple[list[str], int | None]:
    frames: list[str] = []
    last_seq: int | None = None
    for ch in chunks:
        seq = int(ch["seq"])
        frames.append(
            _sse(
                "chunk",
                {"field": ch["field"], "delta": ch["delta"]},
                event_id=seq,
            )
        )
        last_seq = seq
    return frames, last_seq


async def _drain_notify(
    notify_q: asyncio.Queue[str], *, timeout: float = 1.0
) -> None:
    try:
        await asyncio.wait_for(notify_q.get(), timeout=timeout)
        while not notify_q.empty():
            notify_q.get_nowait()
    except TimeoutError:
        pass


async def analysis_event_stream(
    *,
    analysis_id: UUID,
    org_id: UUID,
    after_seq: int,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"

    settings = get_settings()
    last_seq = after_seq
    last_emit = asyncio.get_event_loop().time()

    assert db.app_pool is not None
    listen_conn = await asyncpg.connect(settings.app_user_dsn)
    try:
        await listen_conn.execute(
            "SELECT set_config('app.org_id', $1, false)",
            str(org_id),
        )

        notify_q: asyncio.Queue[str] = asyncio.Queue()

        def _listener(_conn: Any, _pid: int, channel: str, payload: str) -> None:
            notify_q.put_nowait(payload)

        await listen_conn.add_listener("analysis_chunk", _listener)
        await listen_conn.add_listener("analysis_status", _listener)

        status_row, chunks = await _fetch_snapshot(org_id, analysis_id, last_seq)
        frames, new_seq = _emit_chunks(chunks)
        for frame in frames:
            yield frame
        if new_seq is not None:
            last_seq = new_seq
            last_emit = asyncio.get_event_loop().time()

        if status_row and status_row["status"] in TERMINAL:
            yield _terminal_event(status_row)
            return

        while True:
            if await is_disconnected():
                return

            await _drain_notify(notify_q)

            status_row, chunks = await _fetch_snapshot(org_id, analysis_id, last_seq)
            frames, new_seq = _emit_chunks(chunks)
            for frame in frames:
                yield frame
            if new_seq is not None:
                last_seq = new_seq
                last_emit = asyncio.get_event_loop().time()

            if status_row and status_row["status"] in TERMINAL:
                # Ensure all chunks flushed before terminal
                yield _terminal_event(status_row)
                return

            now = asyncio.get_event_loop().time()
            if now - last_emit >= 15:
                yield ": ping\n\n"
                last_emit = now
    finally:
        await listen_conn.close()


def _terminal_event(row: asyncpg.Record) -> str:
    status = row["status"]
    if status == "done":
        result = row["result"]
        if isinstance(result, str):
            result = orjson.loads(result)
        return _sse("done", {"result": result})
    if status == "cancelled":
        return _sse("error", {"code": "cancelled", "message": "analysis cancelled"})
    message = row["error"] or "analysis failed"
    return _sse("error", {"message": message})


def _parse_notify(payload: str) -> dict[str, Any] | None:
    try:
        data = orjson.loads(payload)
    except orjson.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _uuid_field(data: dict[str, Any], key: str) -> UUID | None:
    try:
        return UUID(str(data[key]))
    except (KeyError, ValueError, TypeError):
        return None


async def _drain_notify_queue(
    notify_q: asyncio.Queue[tuple[str, str]], *, timeout: float = 1.0
) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    try:
        events.append(await asyncio.wait_for(notify_q.get(), timeout=timeout))
        while not notify_q.empty():
            events.append(notify_q.get_nowait())
    except TimeoutError:
        pass
    return events


async def _sse_for_call_notify(
    *,
    channel: str,
    data: dict[str, Any],
    org_id: UUID,
) -> str | None:
    from app.services.call_attempt_read import fetch_crm, fetch_list_item

    if channel == "call_attempt_status":
        attempt_id = _uuid_field(data, "attempt_id")
        if attempt_id is None:
            return None
        async with db.tenant_connection(org_id) as conn:
            item = await fetch_list_item(conn, attempt_id)
        return _sse("attempt", item) if item is not None else None

    if channel == "crm_delivery":
        attempt_id = _uuid_field(data, "attempt_id")
        if attempt_id is None:
            return None
        async with db.tenant_connection(org_id) as conn:
            crm = await fetch_crm(conn, attempt_id)
        return _sse("crm", {"attempt_id": str(attempt_id), "crm": crm})

    if channel == "analysis_status":
        attempt_id = _uuid_field(data, "call_attempt_id")
        analysis_id = _uuid_field(data, "analysis_id")
        status = data.get("status")
        if attempt_id is None or analysis_id is None or not isinstance(status, str):
            return None
        return _sse(
            "analysis",
            {
                "attempt_id": str(attempt_id),
                "analysis_id": str(analysis_id),
                "status": status,
            },
        )

    return None


async def call_attempts_event_stream(
    *,
    org_id: UUID,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    """Org-level SSE feed: attempt / crm / analysis status changes."""
    yield "retry: 3000\n\n"

    settings = get_settings()
    last_emit = asyncio.get_event_loop().time()
    org_s = str(org_id)

    listen_conn = await asyncpg.connect(settings.app_user_dsn)
    try:
        await listen_conn.execute(
            "SELECT set_config('app.org_id', $1, false)",
            org_s,
        )

        notify_q: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        def _listener(_conn: Any, _pid: int, channel: str, payload: str) -> None:
            notify_q.put_nowait((channel, payload))

        await listen_conn.add_listener("call_attempt_status", _listener)
        await listen_conn.add_listener("crm_delivery", _listener)
        await listen_conn.add_listener("analysis_status", _listener)

        while True:
            if await is_disconnected():
                return

            for channel, raw in await _drain_notify_queue(notify_q):
                data = _parse_notify(raw)
                if data is None or str(data.get("org_id", "")) != org_s:
                    continue
                frame = await _sse_for_call_notify(
                    channel=channel, data=data, org_id=org_id
                )
                if frame is not None:
                    yield frame
                    last_emit = asyncio.get_event_loop().time()

            now = asyncio.get_event_loop().time()
            if now - last_emit >= 15:
                yield ": ping\n\n"
                last_emit = now
    finally:
        await listen_conn.close()
