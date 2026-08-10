from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import orjson
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth import Principal, require_roles
from app.db import db
from app.errors import AppError
from app.services.webhook_apply import advisory_lock, sweep_buffer

router = APIRouter(tags=["call-attempts"])


class ProviderLinkRequest(BaseModel):
    provider_call_id: str = Field(min_length=1, max_length=128)


class ProviderLinkResponse(BaseModel):
    linked: bool = True
    provider_call_id: str


class AbortRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class CallAttemptMutationResult(BaseModel):
    id: UUID
    status: str


@router.post(
    "/api/call_attempts/{attempt_id}/provider-link",
    response_model=ProviderLinkResponse,
)
async def link_provider_call(
    attempt_id: UUID,
    body: ProviderLinkRequest,
    principal: Principal = Depends(require_roles("worker")),
) -> ProviderLinkResponse:
    async with db.tenant_connection(principal.org_id) as conn:
        await advisory_lock(conn, body.provider_call_id)
        attempt = await conn.fetchrow(
            """
            SELECT id, provider_call_id, status
            FROM call_attempts
            WHERE id = $1
            """,
            attempt_id,
        )
        if attempt is None:
            raise AppError(404, "not_found", "call attempt not found")

        existing = attempt["provider_call_id"]
        if existing is not None:
            if existing == body.provider_call_id:
                return ProviderLinkResponse(
                    linked=True, provider_call_id=body.provider_call_id
                )
            raise AppError(409, "already_linked", "attempt already linked to another provider_call_id")

        # Ensure uniqueness: if another attempt already has this provider_call_id
        conflict = await conn.fetchrow(
            """
            SELECT id FROM call_attempts
            WHERE provider_call_id = $1 AND id <> $2
            """,
            body.provider_call_id,
            attempt_id,
        )
        if conflict is not None:
            raise AppError(409, "already_linked", "provider_call_id already linked")

        await conn.execute(
            """
            UPDATE call_attempts
            SET provider_call_id = $2
            WHERE id = $1
            """,
            attempt_id,
            body.provider_call_id,
        )
        await sweep_buffer(conn, body.provider_call_id)

    return ProviderLinkResponse(linked=True, provider_call_id=body.provider_call_id)


@router.post(
    "/api/call_attempts/{attempt_id}/abort",
    response_model=CallAttemptMutationResult,
)
async def abort_call_attempt(
    attempt_id: UUID,
    body: AbortRequest,
    principal: Principal = Depends(require_roles("worker")),
) -> CallAttemptMutationResult:
    async with db.tenant_connection(principal.org_id) as conn:
        attempt = await conn.fetchrow(
            "SELECT id, status FROM call_attempts WHERE id = $1",
            attempt_id,
        )
        if attempt is None:
            raise AppError(404, "not_found", "call attempt not found")
        if attempt["status"] != "queued":
            raise AppError(409, "not_queued", "attempt is not in queued status")

        outcome = {"reason": body.reason}
        await conn.execute(
            """
            UPDATE call_attempts
            SET status = 'failed',
                ended_at = $2,
                outcome = $3::jsonb
            WHERE id = $1
            """,
            attempt_id,
            datetime.now(timezone.utc),
            orjson.dumps(outcome).decode(),
        )

    return CallAttemptMutationResult(id=attempt_id, status="failed")
