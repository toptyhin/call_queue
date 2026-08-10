from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import Principal, require_roles
from app.db import db
from app.errors import AppError

router = APIRouter(tags=["rpc"])


class ClaimNextContactRequest(BaseModel):
    campaign_id: UUID


class ClaimedContact(BaseModel):
    id: UUID
    phone_e164: str
    attempt_id: UUID


class ClaimNextContactResponse(BaseModel):
    contact: ClaimedContact | None


@router.post("/rpc/claim_next_contact", response_model=ClaimNextContactResponse)
async def claim_next_contact(
    body: ClaimNextContactRequest,
    principal: Principal = Depends(require_roles("worker", "authenticated")),
) -> ClaimNextContactResponse:
    async with db.tenant_connection(principal.org_id) as conn:
        try:
            row = await conn.fetchrow(
                "SELECT * FROM claim_next_contact($1)",
                body.campaign_id,
            )
        except asyncpg.PostgresError as exc:
            # P0002 / no_data_found = campaign not found (see claim_next_contact)
            sqlstate = getattr(exc, "sqlstate", None)
            if sqlstate in {"P0002", "02000"} or "campaign not found" in str(exc):
                raise AppError(404, "not_found", "campaign not found") from exc
            raise

    if row is None:
        return ClaimNextContactResponse(contact=None)

    return ClaimNextContactResponse(
        contact=ClaimedContact(
            id=row["contact_id"],
            phone_e164=row["phone_e164"],
            attempt_id=row["attempt_id"],
        )
    )
