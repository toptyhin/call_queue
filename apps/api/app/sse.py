from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
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

        # Initial replay + status
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
                last_seq,
            )

        for ch in chunks:
            yield _sse(
                "chunk",
                {"field": ch["field"], "delta": ch["delta"]},
                event_id=int(ch["seq"]),
            )
            last_seq = int(ch["seq"])
            last_emit = asyncio.get_event_loop().time()

        if status_row and status_row["status"] in TERMINAL:
            yield _terminal_event(status_row)
            return

        while True:
            if await is_disconnected():
                return

            # Drain notifications with timeout for heartbeat / reread
            try:
                await asyncio.wait_for(notify_q.get(), timeout=1.0)
                while not notify_q.empty():
                    notify_q.get_nowait()
            except asyncio.TimeoutError:
                pass

            async with db.tenant_connection(org_id) as conn:
                new_chunks = await conn.fetch(
                    """
                    SELECT seq, field, delta
                    FROM analysis_chunks
                    WHERE analysis_id = $1 AND seq > $2
                    ORDER BY seq
                    """,
                    analysis_id,
                    last_seq,
                )
                status_row = await conn.fetchrow(
                    "SELECT status, result, error, partial FROM analyses WHERE id = $1",
                    analysis_id,
                )

            for ch in new_chunks:
                yield _sse(
                    "chunk",
                    {"field": ch["field"], "delta": ch["delta"]},
                    event_id=int(ch["seq"]),
                )
                last_seq = int(ch["seq"])
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
