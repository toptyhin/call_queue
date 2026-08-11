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
_NOTIFY_QUEUE_MAXSIZE = 1000
_RETRIABLE = (asyncpg.PostgresConnectionError, OSError, TimeoutError)


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


async def _retry_backoff(attempt: int) -> None:
    await asyncio.sleep(min(0.2 * (2**attempt), 5.0))


def _notify_sink(
    notify_q: asyncio.Queue[Any],
    *,
    with_channel: bool = False,
) -> Callable[..., None]:
    """Return an asyncpg LISTEN callback that never raises into the driver."""

    def _listener(_conn: Any, _pid: int, channel: str, payload: str) -> None:
        try:
            item: Any = (channel, payload) if with_channel else payload
            notify_q.put_nowait(item)
        except Exception:
            log.warning("sse.notify_enqueue_failed", channel=channel, exc_info=True)

    return _listener


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


async def _analysis_event_stream_once(
    *,
    analysis_id: UUID,
    org_id: UUID,
    seq_state: dict[str, int],
    is_disconnected: Callable[[], Awaitable[bool]],
    notify_q: asyncio.Queue[str],
) -> AsyncIterator[str]:
    """Single LISTEN session. Updates seq_state['last_seq'] as chunks are emitted."""
    settings = get_settings()
    last_seq = seq_state["last_seq"]
    last_emit = asyncio.get_event_loop().time()

    listen_conn = await asyncpg.connect(settings.app_user_dsn)
    try:
        await listen_conn.execute(
            "SELECT set_config('app.org_id', $1, false)",
            str(org_id),
        )

        listener = _notify_sink(notify_q, with_channel=False)
        await listen_conn.add_listener("analysis_chunk", listener)
        await listen_conn.add_listener("analysis_status", listener)

        status_row, chunks = await _fetch_snapshot(org_id, analysis_id, last_seq)
        frames, new_seq = _emit_chunks(chunks)
        for frame in frames:
            yield frame
        if new_seq is not None:
            last_seq = new_seq
            seq_state["last_seq"] = last_seq
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
                seq_state["last_seq"] = last_seq
                last_emit = asyncio.get_event_loop().time()

            if status_row and status_row["status"] in TERMINAL:
                yield _terminal_event(status_row)
                return

            now = asyncio.get_event_loop().time()
            if now - last_emit >= 15:
                yield ": ping\n\n"
                last_emit = now
    finally:
        await listen_conn.close()


async def analysis_event_stream(
    *,
    analysis_id: UUID,
    org_id: UUID,
    after_seq: int,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    """SSE for one analysis; reconnects LISTEN on transient DB/network errors."""
    yield "retry: 3000\n\n"

    notify_q: asyncio.Queue[str] = asyncio.Queue(maxsize=_NOTIFY_QUEUE_MAXSIZE)
    seq_state = {"last_seq": after_seq}
    attempt = 0

    while True:
        if await is_disconnected():
            return
        try:
            async for frame in _analysis_event_stream_once(
                analysis_id=analysis_id,
                org_id=org_id,
                seq_state=seq_state,
                is_disconnected=is_disconnected,
                notify_q=notify_q,
            ):
                attempt = 0
                yield frame
            return
        except asyncio.CancelledError:
            raise
        except _RETRIABLE:
            log.warning(
                "sse.stream_retry",
                stream="analysis",
                analysis_id=str(analysis_id),
                attempt=attempt,
                exc_info=True,
            )
            await _retry_backoff(attempt)
            attempt += 1
        except Exception:
            log.warning(
                "sse.stream_error",
                stream="analysis",
                analysis_id=str(analysis_id),
                exc_info=True,
            )
            raise


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
        log.warning("sse.notify_bad_payload", reason="json")
        return None
    if not isinstance(data, dict):
        log.warning("sse.notify_bad_payload", reason="not_object")
        return None
    return data


def _uuid_field(
    data: dict[str, Any],
    key: str,
    *,
    channel: str | None = None,
) -> UUID | None:
    try:
        return UUID(str(data[key]))
    except (KeyError, ValueError, TypeError):
        log.warning(
            "sse.notify_bad_payload",
            reason="uuid",
            field=key,
            channel=channel,
        )
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
        attempt_id = _uuid_field(data, "attempt_id", channel=channel)
        if attempt_id is None:
            return None
        async with db.tenant_connection(org_id) as conn:
            item = await fetch_list_item(conn, attempt_id)
        return _sse("attempt", item) if item is not None else None

    if channel == "crm_delivery":
        attempt_id = _uuid_field(data, "attempt_id", channel=channel)
        if attempt_id is None:
            return None
        async with db.tenant_connection(org_id) as conn:
            crm = await fetch_crm(conn, attempt_id)
        return _sse("crm", {"attempt_id": str(attempt_id), "crm": crm})

    if channel == "analysis_status":
        attempt_id = _uuid_field(data, "call_attempt_id", channel=channel)
        analysis_id = _uuid_field(data, "analysis_id", channel=channel)
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


async def _call_attempts_event_stream_once(
    *,
    org_id: UUID,
    org_s: str,
    is_disconnected: Callable[[], Awaitable[bool]],
    notify_q: asyncio.Queue[tuple[str, str]],
    last_emit_state: dict[str, float],
) -> AsyncIterator[str]:
    settings = get_settings()
    last_emit = last_emit_state["last_emit"]

    listen_conn = await asyncpg.connect(settings.app_user_dsn)
    try:
        await listen_conn.execute(
            "SELECT set_config('app.org_id', $1, false)",
            org_s,
        )

        listener = _notify_sink(notify_q, with_channel=True)
        await listen_conn.add_listener("call_attempt_status", listener)
        await listen_conn.add_listener("crm_delivery", listener)
        await listen_conn.add_listener("analysis_status", listener)

        while True:
            if await is_disconnected():
                return

            for channel, raw in await _drain_notify_queue(notify_q):
                data = _parse_notify(raw)
                if data is None:
                    continue
                if str(data.get("org_id", "")) != org_s:
                    log.info(
                        "sse.notify_org_mismatch",
                        channel=channel,
                        stream="call_attempts",
                    )
                    continue
                try:
                    frame = await _sse_for_call_notify(
                        channel=channel, data=data, org_id=org_id
                    )
                except _RETRIABLE:
                    log.warning(
                        "sse.notify_apply_failed",
                        channel=channel,
                        exc_info=True,
                    )
                    frame = None
                if frame is not None:
                    yield frame
                    last_emit = asyncio.get_event_loop().time()
                    last_emit_state["last_emit"] = last_emit

            now = asyncio.get_event_loop().time()
            if now - last_emit >= 15:
                yield ": ping\n\n"
                last_emit = now
                last_emit_state["last_emit"] = last_emit
    finally:
        await listen_conn.close()


async def call_attempts_event_stream(
    *,
    org_id: UUID,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    """Org-level SSE feed: attempt / crm / analysis status changes."""
    yield "retry: 3000\n\n"

    org_s = str(org_id)
    notify_q: asyncio.Queue[tuple[str, str]] = asyncio.Queue(
        maxsize=_NOTIFY_QUEUE_MAXSIZE
    )
    last_emit_state = {"last_emit": asyncio.get_event_loop().time()}
    attempt = 0

    while True:
        if await is_disconnected():
            return
        try:
            async for frame in _call_attempts_event_stream_once(
                org_id=org_id,
                org_s=org_s,
                is_disconnected=is_disconnected,
                notify_q=notify_q,
                last_emit_state=last_emit_state,
            ):
                attempt = 0
                yield frame
            return
        except asyncio.CancelledError:
            raise
        except _RETRIABLE:
            log.warning(
                "sse.stream_retry",
                stream="call_attempts",
                org_id=org_s,
                attempt=attempt,
                exc_info=True,
            )
            await _retry_backoff(attempt)
            attempt += 1
        except Exception:
            log.warning(
                "sse.stream_error",
                stream="call_attempts",
                org_id=org_s,
                exc_info=True,
            )
            raise
