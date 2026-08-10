from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.errors import AppError

router = APIRouter(tags=["dev"])


class DevTokenRequest(BaseModel):
    sub: str = Field(min_length=1)
    org_id: UUID
    role: str = Field(pattern="^(worker|authenticated)$")
    expires_in: int = Field(default=86400, ge=60)


class DevTokenResponse(BaseModel):
    token: str


@router.post("/dev/token", response_model=DevTokenResponse)
async def mint_dev_token(
    body: DevTokenRequest,
    settings: Settings = Depends(get_settings),
) -> DevTokenResponse:
    if not settings.dev_token_enabled:
        raise AppError(404, "not_found", "dev token endpoint disabled")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": body.sub,
        "org_id": str(body.org_id),
        "role": body.role,
        "iat": now,
        "exp": now + timedelta(seconds=body.expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return DevTokenResponse(token=token)
