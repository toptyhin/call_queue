"""Read API for call attempts list/detail + org SSE feed (UI extension)."""

from __future__ import annotations

import binascii
import re
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import Principal, require_roles
from app.db import db
from app.errors import AppError
from app.services.call_attempt_read import (
    decode_cursor,
    fetch_detail,
    fetch_list_page,
)
from app.sse import call_attempts_event_stream

router = APIRouter(tags=["call-attempts"])

AuthenticatedPrincipal = Annotated[
    Principal, Depends(require_roles("authenticated"))
]

CALL_ATTEMPT_STATUSES = frozenset(
    {"queued", "dialing", "in_progress", "completed", "failed", "no_answer"}
)
PHONE_PREFIX_RE = re.compile(r"^\+?[0-9]{1,15}$")


class CallAttemptListItem(BaseModel):
    id: UUID
    status: str
    phone: str
    campaign_name: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime


class CallAttemptListResponse(BaseModel):
    items: list[CallAttemptListItem]
    next_cursor: str | None = None


class CallAttemptContact(BaseModel):
    phone: str
    timezone: str


class StatusHistoryItem(BaseModel):
    at: datetime
    status: str
    source: str
    event_type: str | None = None
    sequence: int | None = None


class CrmDelivery(BaseModel):
    state: str
    attempts: int
    delivered_at: datetime | None = None
    last_error: str | None = None
    next_attempt_at: datetime | None = None


class AnalysisSummary(BaseModel):
    id: UUID
    status: str
    result: dict[str, Any] | None = None
    partial: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime


class CallAttemptDetail(BaseModel):
    id: UUID
    status: str
    provider_call_id: str | None = None
    campaign_name: str
    contact: CallAttemptContact
    started_at: datetime | None = None
    ended_at: datetime | None = None
    outcome: dict[str, Any] | None = None
    transcript: str | None = None
    status_history: list[StatusHistoryItem]
    analyses: list[AnalysisSummary]
    crm: CrmDelivery | None = None
    created_at: datetime


@router.get("/api/call_attempts", response_model=CallAttemptListResponse)
async def list_call_attempts(
    principal: AuthenticatedPrincipal,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1),
    status: str | None = Query(default=None, min_length=1),
    phone: str | None = Query(default=None, min_length=1, max_length=16),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
) -> CallAttemptListResponse:
    cursor_key: tuple[datetime, UUID] | None = None
    if cursor:
        try:
            cursor_key = decode_cursor(cursor)
        except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
            raise AppError(422, "validation_error", "invalid cursor") from exc

    if status is not None and status not in CALL_ATTEMPT_STATUSES:
        raise AppError(422, "validation_error", "invalid status")
    if phone is not None and PHONE_PREFIX_RE.fullmatch(phone) is None:
        raise AppError(422, "validation_error", "invalid phone prefix")

    async with db.tenant_connection(principal.org_id) as conn:
        items, next_cursor = await fetch_list_page(
            conn,
            limit=limit,
            cursor=cursor_key,
            status=status,
            phone=phone,
            created_from=created_from,
            created_to=created_to,
        )

    return CallAttemptListResponse(
        items=[CallAttemptListItem(**item) for item in items],
        next_cursor=next_cursor,
    )


@router.get("/api/call_attempts/stream")
async def stream_call_attempts(
    request: Request,
    principal: AuthenticatedPrincipal,
) -> StreamingResponse:
    generator = call_attempts_event_stream(
        org_id=principal.org_id,
        is_disconnected=request.is_disconnected,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/api/call_attempts/{attempt_id}",
    response_model=CallAttemptDetail,
)
async def get_call_attempt(
    attempt_id: UUID,
    principal: AuthenticatedPrincipal,
) -> CallAttemptDetail:
    async with db.tenant_connection(principal.org_id) as conn:
        detail = await fetch_detail(conn, attempt_id)
    if detail is None:
        raise AppError(404, "not_found", "call attempt not found")
    return CallAttemptDetail(**detail)
