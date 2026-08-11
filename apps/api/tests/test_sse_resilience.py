from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import asyncpg
import orjson
import pytest

from app.sse import (
    _NOTIFY_QUEUE_MAXSIZE,
    _notify_sink,
    _parse_notify,
    _uuid_field,
    analysis_event_stream,
    call_attempts_event_stream,
)

ORG = UUID("11111111-1111-4111-8111-111111111111")


def test_parse_notify_valid() -> None:
    payload = orjson.dumps({"org_id": str(ORG), "status": "done"}).decode()
    assert _parse_notify(payload) == {"org_id": str(ORG), "status": "done"}


def test_parse_notify_bad_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert _parse_notify("not-json{") is None
    captured = capsys.readouterr().out + capsys.readouterr().err
    assert "sse.notify_bad_payload" in captured
    assert "reason=json" in captured


def test_parse_notify_not_object() -> None:
    assert _parse_notify(orjson.dumps([1, 2, 3]).decode()) is None


def test_uuid_field_valid() -> None:
    uid = uuid4()
    assert _uuid_field({"attempt_id": str(uid)}, "attempt_id") == uid


def test_uuid_field_invalid() -> None:
    assert _uuid_field({"attempt_id": "not-a-uuid"}, "attempt_id") is None
    assert _uuid_field({}, "attempt_id") is None


def test_notify_sink_queue_full_does_not_raise() -> None:
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    q.put_nowait("full")
    listener = _notify_sink(q, with_channel=False)
    # Must not raise into asyncpg callback path.
    listener(None, 1, "analysis_chunk", "dropped")
    assert q.qsize() == 1


def test_notify_sink_with_channel() -> None:
    q: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=_NOTIFY_QUEUE_MAXSIZE)
    listener = _notify_sink(q, with_channel=True)
    listener(None, 1, "crm_delivery", '{"ok":true}')
    assert q.get_nowait() == ("crm_delivery", '{"ok":true}')


@pytest.mark.asyncio
async def test_analysis_stream_retries_after_connection_error() -> None:
    analysis_id = uuid4()
    done_row = MagicMock()
    done_row.__getitem__ = lambda self, key: {
        "status": "done",
        "result": {"summary": "ok", "lead_score": 1},
        "error": None,
    }[key]

    chunk = MagicMock()
    chunk.__getitem__ = lambda self, key: {
        "seq": 1,
        "field": "summary",
        "delta": "hi",
    }[key]

    fetch_calls = {"n": 0}

    async def fake_fetch(
        org_id: UUID, analysis_id: UUID, after_seq: int
    ) -> tuple[Any, list[Any]]:
        fetch_calls["n"] += 1
        if fetch_calls["n"] == 1:
            raise asyncpg.PostgresConnectionError("boom")
        return done_row, [chunk]

    listen_conn = AsyncMock()
    listen_conn.execute = AsyncMock()
    listen_conn.add_listener = AsyncMock()
    listen_conn.close = AsyncMock()

    async def never_disconnected() -> bool:
        return False

    with (
        patch("app.sse._fetch_snapshot", side_effect=fake_fetch),
        patch("app.sse.asyncpg.connect", AsyncMock(return_value=listen_conn)),
        patch("app.sse._retry_backoff", AsyncMock()),
    ):
        frames: list[str] = []
        async for frame in analysis_event_stream(
            analysis_id=analysis_id,
            org_id=ORG,
            after_seq=0,
            is_disconnected=never_disconnected,
        ):
            frames.append(frame)

    assert fetch_calls["n"] >= 2
    assert frames[0] == "retry: 3000\n\n"
    assert any("event: chunk" in f for f in frames)
    assert any("event: done" in f for f in frames)


@pytest.mark.asyncio
async def test_call_attempts_stream_isolates_notify_apply_failure() -> None:
    org_s = str(ORG)
    good_payload = orjson.dumps(
        {
            "org_id": org_s,
            "call_attempt_id": str(uuid4()),
            "analysis_id": str(uuid4()),
            "status": "streaming",
        }
    ).decode()
    bad_payload = orjson.dumps(
        {
            "org_id": org_s,
            "attempt_id": str(uuid4()),
        }
    ).decode()

    apply_calls: list[str] = []

    async def fake_apply(
        *, channel: str, data: dict[str, Any], org_id: UUID
    ) -> str | None:
        apply_calls.append(channel)
        if channel == "call_attempt_status":
            raise asyncpg.PostgresConnectionError("fetch failed")
        return f"event: analysis\ndata: {orjson.dumps(data).decode()}\n\n"

    listen_conn = AsyncMock()
    listen_conn.execute = AsyncMock()
    listen_conn.close = AsyncMock()

    # Capture the listener registered with with_channel=True and feed events.
    registered: list[Any] = []

    async def add_listener(channel: str, callback: Any) -> None:
        registered.append((channel, callback))

    listen_conn.add_listener = add_listener

    stop = {"n": 0}

    async def disconnect_after_frames() -> bool:
        # Allow one drain cycle after events are queued, then stop.
        stop["n"] += 1
        return stop["n"] > 5

    with (
        patch("app.sse.asyncpg.connect", AsyncMock(return_value=listen_conn)),
        patch("app.sse._sse_for_call_notify", side_effect=fake_apply),
        patch("app.sse._drain_notify_queue") as drain,
    ):
        # First drain returns both events; later drains empty (timeout → []).
        drain.side_effect = [
            [
                ("call_attempt_status", bad_payload),
                ("analysis_status", good_payload),
            ],
            [],
            [],
            [],
            [],
            [],
        ]

        frames: list[str] = []
        async for frame in call_attempts_event_stream(
            org_id=ORG,
            is_disconnected=disconnect_after_frames,
        ):
            frames.append(frame)

    assert "call_attempt_status" in apply_calls
    assert "analysis_status" in apply_calls
    assert any("event: analysis" in f for f in frames)
