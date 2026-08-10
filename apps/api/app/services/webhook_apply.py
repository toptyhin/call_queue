"""Shared pipeline: apply buffered/matched webhook events to call_attempts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg
import orjson

KNOWN_TYPES = {
    "call.queued": "queued",
    "call.dialing": "dialing",
    "call.answered": "in_progress",
    "call.completed": "completed",
    "call.failed": "failed",
    "call.no_answer": "no_answer",
}

TERMINAL = frozenset({"completed", "failed", "no_answer"})


async def advisory_lock(conn: asyncpg.Connection, provider_call_id: str) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))",
        provider_call_id,
    )


async def find_attempt_by_provider_call_id(
    conn: asyncpg.Connection, provider_call_id: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, org_id, status, last_applied_sequence, provider_call_id
        FROM call_attempts
        WHERE provider_call_id = $1
        """,
        provider_call_id,
    )


async def find_attempt_for_fallback_link(
    conn: asyncpg.Connection, attempt_id: UUID
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, org_id, status, last_applied_sequence, provider_call_id
        FROM call_attempts
        WHERE id = $1
        """,
        attempt_id,
    )


async def link_provider_call_id(
    conn: asyncpg.Connection, attempt_id: UUID, provider_call_id: str
) -> None:
    await conn.execute(
        """
        UPDATE call_attempts
        SET provider_call_id = $2
        WHERE id = $1
        """,
        attempt_id,
        provider_call_id,
    )


async def apply_event_to_attempt(
    conn: asyncpg.Connection,
    *,
    event_row_id: UUID,
    attempt: asyncpg.Record,
    sequence: int,
    event_type: str,
    data: dict[str, Any],
    occurred_at: datetime,
) -> bool:
    """Apply one event if sequence/terminal guards pass. Returns True if applied."""
    target = KNOWN_TYPES.get(event_type)
    if target is None:
        return False

    if sequence <= int(attempt["last_applied_sequence"]):
        return False

    if attempt["status"] in TERMINAL:
        return False

    started_at = attempt.get("started_at") if "started_at" in attempt else None
    ended_at = None
    outcome = None
    transcript = None
    new_status = target

    if event_type == "call.answered":
        started_at = occurred_at
    elif event_type in ("call.completed", "call.failed", "call.no_answer"):
        ended_at = occurred_at
        outcome = data
        if event_type == "call.completed":
            tr = data.get("transcript")
            if isinstance(tr, str):
                transcript = tr

    await conn.execute(
        """
        UPDATE call_attempts SET
            status = $2,
            started_at = COALESCE($3, started_at),
            ended_at = COALESCE($4, ended_at),
            outcome = COALESCE($5::jsonb, outcome),
            transcript = COALESCE($6, transcript),
            last_applied_sequence = $7
        WHERE id = $1
        """,
        attempt["id"],
        new_status,
        started_at,
        ended_at,
        orjson.dumps(outcome).decode() if outcome is not None else None,
        transcript,
        sequence,
    )
    await conn.execute(
        """
        UPDATE webhook_events
        SET applied_at = now(), call_attempt_id = $2
        WHERE id = $1
        """,
        event_row_id,
        attempt["id"],
    )
    return True


async def sweep_buffer(conn: asyncpg.Connection, provider_call_id: str) -> None:
    """Apply buffered events for a linked provider_call_id in sequence order."""
    attempt = await find_attempt_by_provider_call_id(conn, provider_call_id)
    if attempt is None:
        return

    rows = await conn.fetch(
        """
        SELECT id, sequence, type, payload, applied_at
        FROM webhook_events
        WHERE provider_call_id = $1
        ORDER BY sequence ASC
        """,
        provider_call_id,
    )
    for row in rows:
        if row["applied_at"] is not None:
            continue
        # Refresh attempt state each time
        attempt = await find_attempt_by_provider_call_id(conn, provider_call_id)
        if attempt is None:
            return
        payload = row["payload"]
        if isinstance(payload, str):
            payload = orjson.loads(payload)
        data = payload.get("data") or {}
        occurred_raw = payload.get("occurred_at")
        if isinstance(occurred_raw, str):
            occurred_at = datetime.fromisoformat(occurred_raw.replace("Z", "+00:00"))
        else:
            occurred_at = datetime.now(timezone.utc)
        applied = await apply_event_to_attempt(
            conn,
            event_row_id=row["id"],
            attempt=attempt,
            sequence=int(row["sequence"]),
            event_type=row["type"],
            data=data if isinstance(data, dict) else {},
            occurred_at=occurred_at,
        )
        if not applied and row["type"] in KNOWN_TYPES:
            # Mark as inspected but not applied? Spec: save, don't apply.
            # Leave applied_at NULL for stale/terminal so we don't re-process forever?
            # Re-processing is cheap; but sequence/terminal guards make it a no-op.
            # Mark applied_at for known types that failed guards to avoid infinite sweeps.
            await conn.execute(
                """
                UPDATE webhook_events
                SET applied_at = now(), call_attempt_id = $2
                WHERE id = $1 AND applied_at IS NULL
                """,
                row["id"],
                attempt["id"],
            )
