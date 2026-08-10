from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import Principal, require_roles
from app.db import db
from app.errors import AppError
from app.services.analysis_partial import row_to_analysis
from app.sse import analysis_event_stream
from app.tasks.stream_consumer import wake_analysis_dispatcher

router = APIRouter(tags=["analyses"])


class CreateAnalysisRequest(BaseModel):
    call_attempt_id: UUID


class AnalysisCreated(BaseModel):
    id: UUID
    status: str = "queued"


class AnalysisCancelled(BaseModel):
    id: UUID
    status: str = "cancelled"


class AnalysisOut(BaseModel):
    id: UUID
    call_attempt_id: UUID
    status: str
    result: dict[str, Any] | None = None
    partial: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime


@router.post("/api/analyses", response_model=AnalysisCreated)
async def create_analysis(
    body: CreateAnalysisRequest,
    principal: Principal = Depends(require_roles("authenticated", "worker")),
) -> AnalysisCreated:
    async with db.tenant_connection(principal.org_id) as conn:
        attempt = await conn.fetchrow(
            """
            SELECT id, status, transcript
            FROM call_attempts
            WHERE id = $1
            """,
            body.call_attempt_id,
        )
        if attempt is None:
            raise AppError(404, "not_found", "call attempt not found")
        if attempt["status"] != "completed":
            raise AppError(409, "attempt_not_completed", "call attempt is not completed")
        if not attempt["transcript"]:
            raise AppError(409, "no_transcript", "call attempt has no transcript")

        row = await conn.fetchrow(
            """
            INSERT INTO analyses (org_id, call_attempt_id, status)
            VALUES ($1, $2, 'queued')
            RETURNING id, status
            """,
            principal.org_id,
            body.call_attempt_id,
        )
        assert row is not None
        analysis_id = row["id"]

    wake_analysis_dispatcher()
    return AnalysisCreated(id=analysis_id, status="queued")


@router.get("/api/analyses/{analysis_id}", response_model=AnalysisOut)
async def get_analysis(
    analysis_id: UUID,
    principal: Principal = Depends(require_roles("authenticated", "worker")),
) -> AnalysisOut:
    async with db.tenant_connection(principal.org_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT id, call_attempt_id, status, result, partial, error, created_at
            FROM analyses
            WHERE id = $1
            """,
            analysis_id,
        )
    if row is None:
        raise AppError(404, "not_found", "analysis not found")
    return AnalysisOut(**row_to_analysis(row))


@router.post("/api/analyses/{analysis_id}/cancel", response_model=AnalysisCancelled)
async def cancel_analysis(
    analysis_id: UUID,
    principal: Principal = Depends(require_roles("authenticated", "worker")),
) -> AnalysisCancelled:
    async with db.tenant_connection(principal.org_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, status FROM analyses WHERE id = $1",
            analysis_id,
        )
        if row is None:
            raise AppError(404, "not_found", "analysis not found")
        if row["status"] in ("done", "error"):
            raise AppError(409, "analysis_terminal", "analysis already terminal")
        if row["status"] == "cancelled":
            return AnalysisCancelled(id=analysis_id, status="cancelled")
        await conn.execute(
            """
            UPDATE analyses
            SET status = 'cancelled',
                cancel_requested = true,
                updated_at = now()
            WHERE id = $1
            """,
            analysis_id,
        )
    return AnalysisCancelled(id=analysis_id, status="cancelled")


@router.get("/api/analyses/{analysis_id}/stream")
async def stream_analysis(
    analysis_id: UUID,
    request: Request,
    principal: Principal = Depends(require_roles("authenticated", "worker")),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    async with db.tenant_connection(principal.org_id) as conn:
        row = await conn.fetchrow(
            "SELECT id FROM analyses WHERE id = $1",
            analysis_id,
        )
    if row is None:
        raise AppError(404, "not_found", "analysis not found")

    after_seq = 0
    if last_event_id:
        try:
            after_seq = int(last_event_id)
        except ValueError:
            after_seq = 0

    generator = analysis_event_stream(
        analysis_id=analysis_id,
        org_id=principal.org_id,
        after_seq=after_seq,
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
