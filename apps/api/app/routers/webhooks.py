from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
import orjson
import structlog
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import db
from app.errors import AppError
from app.services.webhook_apply import (
    KNOWN_TYPES,
    advisory_lock,
    apply_event_to_attempt,
    find_attempt_by_provider_call_id,
    find_attempt_for_fallback_link,
    link_provider_call_id,
    sweep_buffer,
)

router = APIRouter(tags=["webhooks"])
log = structlog.get_logger(__name__)


class WebhookAccepted(BaseModel):
    received: bool = True


class WebhookEventIn(BaseModel):
    event_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    type: str = Field(min_length=1)
    occurred_at: datetime
    data: dict[str, Any]


def _verify_signature(raw: bytes, signature: str | None, secret: str) -> None:
    if not signature or not signature.startswith("sha256="):
        raise AppError(401, "invalid_signature", "missing or malformed X-Signature")
    digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    expected = "sha256=" + digest
    if not hmac.compare_digest(expected, signature):
        raise AppError(401, "invalid_signature", "invalid webhook signature")


async def _try_fallback_link(
    conn: asyncpg.Connection, event: WebhookEventIn
) -> asyncpg.Record | None:
    """Link via data.attempt_id when provider_call_id is unknown; sweep buffer."""
    raw_attempt_id = event.data.get("attempt_id")
    if not raw_attempt_id:
        return None
    try:
        attempt_uuid = UUID(str(raw_attempt_id))
    except ValueError:
        return None

    candidate = await find_attempt_for_fallback_link(conn, attempt_uuid)
    if (
        candidate is None
        or candidate["provider_call_id"] is not None
        or candidate["status"] != "queued"
    ):
        return None

    await link_provider_call_id(conn, candidate["id"], event.call_id)
    attempt = await find_attempt_by_provider_call_id(conn, event.call_id)
    structlog.contextvars.bind_contextvars(attempt_id=str(candidate["id"]))
    await sweep_buffer(conn, event.call_id)
    log.info("webhook.fallback_linked")
    return attempt


async def _apply_if_known(
    conn: asyncpg.Connection,
    event: WebhookEventIn,
    event_row_id: UUID,
    attempt: asyncpg.Record,
) -> None:
    if event.type not in KNOWN_TYPES:
        log.info("webhook.unknown_type")
        return

    # Need full attempt fields for apply
    attempt = await conn.fetchrow(
        """
        SELECT id, org_id, status, last_applied_sequence, provider_call_id, started_at
        FROM call_attempts WHERE id = $1
        """,
        attempt["id"],
    )
    assert attempt is not None
    await apply_event_to_attempt(
        conn,
        event_row_id=event_row_id,
        attempt=attempt,
        sequence=event.sequence,
        event_type=event.type,
        data=event.data,
        occurred_at=event.occurred_at,
    )
    log.info("webhook.processed", type=event.type, sequence=event.sequence)


@router.post("/webhooks/calls", response_model=WebhookAccepted)
async def receive_call_webhook(
    request: Request,
    x_signature: str | None = Header(default=None, alias="X-Signature"),
) -> WebhookAccepted:
    settings = get_settings()
    raw = await request.body()
    _verify_signature(raw, x_signature, settings.webhook_secret)

    try:
        payload = orjson.loads(raw)
        event = WebhookEventIn.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise AppError(422, "validation_error", str(exc)) from exc

    structlog.contextvars.bind_contextvars(
        provider_event_id=event.event_id,
        call_id=event.call_id,
    )

    async with db.webhook_connection() as conn:
        await advisory_lock(conn, event.call_id)

        inserted = await conn.fetchrow(
            """
            INSERT INTO webhook_events (
                provider_event_id, provider_call_id, sequence, type, payload
            )
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (provider_event_id) DO NOTHING
            RETURNING id
            """,
            event.event_id,
            event.call_id,
            event.sequence,
            event.type,
            raw.decode("utf-8"),
        )
        if inserted is None:
            log.info("webhook.duplicate")
            return WebhookAccepted(received=True)

        event_row_id: UUID = inserted["id"]
        attempt = await find_attempt_by_provider_call_id(conn, event.call_id)
        if attempt is None:
            attempt = await _try_fallback_link(conn, event)
            if attempt is not None:
                # Sweep already applied buffered events including this one.
                return WebhookAccepted(received=True)

        if attempt is None:
            log.info("webhook.buffered")
            return WebhookAccepted(received=True)

        structlog.contextvars.bind_contextvars(attempt_id=str(attempt["id"]))
        await _apply_if_known(conn, event, event_row_id, attempt)

    return WebhookAccepted(received=True)
