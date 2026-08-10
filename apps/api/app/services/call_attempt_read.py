"""Read-models for call attempt list/detail (UI extension)."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
import orjson

from app.services.analysis_partial import row_to_analysis
from app.services.webhook_apply import KNOWN_TYPES

CURSOR_SEP = "|"


def encode_cursor(created_at: datetime, attempt_id: UUID) -> str:
    raw = f"{created_at.isoformat()}{CURSOR_SEP}{attempt_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    ts_s, id_s = raw.split(CURSOR_SEP, 1)
    return datetime.fromisoformat(ts_s), UUID(id_s)


def _json_obj(val: Any) -> dict[str, Any] | None:
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    if isinstance(val, (bytes, str)):
        parsed = orjson.loads(val)
        return parsed if isinstance(parsed, dict) else None
    return dict(val)


def _iso(val: datetime | None) -> str | None:
    return val.isoformat() if val is not None else None


def list_item_from_row(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "phone": row["phone"],
        "campaign_name": row["campaign_name"],
        "started_at": _iso(row["started_at"]),
        "ended_at": _iso(row["ended_at"]),
        "created_at": _iso(row["created_at"]),
    }


def crm_from_row(row: asyncpg.Record | None) -> dict[str, Any] | None:
    if row is None:
        return None
    if row["delivered_at"] is not None:
        state = "delivered"
    elif int(row["attempts"]) > 0:
        state = "retrying"
    else:
        state = "pending"
    return {
        "state": state,
        "attempts": int(row["attempts"]),
        "delivered_at": row["delivered_at"],
        "last_error": row["last_error"],
        "next_attempt_at": row["next_attempt_at"],
    }


async def fetch_list_item(
    conn: asyncpg.Connection, attempt_id: UUID
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT
            a.id, a.status, a.started_at, a.ended_at, a.created_at,
            c.phone_e164 AS phone,
            camp.name AS campaign_name
        FROM call_attempts a
        JOIN contacts c ON c.id = a.contact_id
        JOIN campaigns camp ON camp.id = a.campaign_id
        WHERE a.id = $1
        """,
        attempt_id,
    )
    return list_item_from_row(row) if row is not None else None


async def fetch_crm(
    conn: asyncpg.Connection, attempt_id: UUID
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT delivered_at, attempts, last_error, next_attempt_at
        FROM crm_outbox
        WHERE attempt_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        attempt_id,
    )
    return crm_from_row(row)


def _event_at(payload: dict[str, Any] | None, received_at: datetime) -> datetime:
    if payload and isinstance(payload.get("occurred_at"), str):
        try:
            return datetime.fromisoformat(
                payload["occurred_at"].replace("Z", "+00:00")
            )
        except ValueError:
            pass
    return received_at


async def build_status_history(
    conn: asyncpg.Connection, attempt: asyncpg.Record
) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = [
        {
            "at": attempt["created_at"],
            "status": "queued",
            "source": "claim",
            "event_type": None,
            "sequence": None,
        }
    ]

    provider_call_id = attempt["provider_call_id"]
    if provider_call_id:
        events = await conn.fetch(
            """
            SELECT sequence, type, payload, received_at, call_attempt_id
            FROM webhook_events
            WHERE call_attempt_id = $1
               OR provider_call_id = $2
            ORDER BY sequence ASC
            """,
            attempt["id"],
            provider_call_id,
        )
    else:
        events = await conn.fetch(
            """
            SELECT sequence, type, payload, received_at, call_attempt_id
            FROM webhook_events
            WHERE call_attempt_id = $1
            ORDER BY sequence ASC
            """,
            attempt["id"],
        )

    seen_seq: set[int] = set()
    for ev in events:
        etype = ev["type"]
        if etype not in KNOWN_TYPES:
            continue
        seq = int(ev["sequence"])
        if seq in seen_seq:
            continue
        seen_seq.add(seq)
        payload = _json_obj(ev["payload"])
        history.append(
            {
                "at": _event_at(payload, ev["received_at"]),
                "status": KNOWN_TYPES[etype],
                "source": "webhook",
                "event_type": etype,
                "sequence": seq,
            }
        )

    has_terminal_webhook = any(
        h["source"] == "webhook" and h["status"] in ("completed", "failed", "no_answer")
        for h in history
    )
    if (
        attempt["status"] == "failed"
        and not has_terminal_webhook
        and attempt["ended_at"] is not None
    ):
        history.append(
            {
                "at": attempt["ended_at"],
                "status": "failed",
                "source": "abort",
                "event_type": None,
                "sequence": None,
            }
        )

    return history


async def fetch_detail(
    conn: asyncpg.Connection, attempt_id: UUID
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT
            a.id, a.status, a.provider_call_id, a.started_at, a.ended_at,
            a.outcome, a.transcript, a.created_at,
            c.phone_e164 AS phone, c.timezone AS timezone,
            camp.name AS campaign_name
        FROM call_attempts a
        JOIN contacts c ON c.id = a.contact_id
        JOIN campaigns camp ON camp.id = a.campaign_id
        WHERE a.id = $1
        """,
        attempt_id,
    )
    if row is None:
        return None

    analyses_rows = await conn.fetch(
        """
        SELECT id, call_attempt_id, status, result, partial, error, created_at
        FROM analyses
        WHERE call_attempt_id = $1
        ORDER BY created_at DESC
        """,
        attempt_id,
    )
    analyses = []
    for ar in analyses_rows:
        mapped = row_to_analysis(ar)
        analyses.append(
            {
                "id": mapped["id"],
                "status": mapped["status"],
                "result": mapped["result"],
                "partial": mapped["partial"],
                "error": mapped["error"],
                "created_at": mapped["created_at"],
            }
        )

    return {
        "id": row["id"],
        "status": row["status"],
        "provider_call_id": row["provider_call_id"],
        "campaign_name": row["campaign_name"],
        "contact": {"phone": row["phone"], "timezone": row["timezone"]},
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "outcome": _json_obj(row["outcome"]),
        "transcript": row["transcript"],
        "status_history": await build_status_history(conn, row),
        "analyses": analyses,
        "crm": await fetch_crm(conn, attempt_id),
        "created_at": row["created_at"],
    }
