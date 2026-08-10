from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import jwt
from fastapi import APIRouter, Cookie, Depends, Header, Response
from pydantic import BaseModel, Field

from app.auth import DEV_TOKEN_COOKIE, decode_token, extract_bearer_or_cookie
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


class DevSessionResponse(BaseModel):
    authenticated: bool
    sub: str | None = None
    org_id: UUID | None = None
    role: Literal["worker", "authenticated"] | None = None


class DevLogoutResponse(BaseModel):
    ok: bool = True


def _set_dev_cookie(
    response: Response, token: str, *, max_age: int, settings: Settings
) -> None:
    response.set_cookie(
        key=DEV_TOKEN_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=max_age,
        secure=settings.cookie_secure,
    )


@router.post("/dev/token", response_model=DevTokenResponse)
async def mint_dev_token(
    body: DevTokenRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> DevTokenResponse:
    if not settings.dev_token_enabled:
        raise AppError(404, "not_found", "dev token endpoint disabled")

    now = datetime.now(UTC)
    payload = {
        "sub": body.sub,
        "org_id": str(body.org_id),
        "role": body.role,
        "iat": now,
        "exp": now + timedelta(seconds=body.expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    _set_dev_cookie(response, token, max_age=body.expires_in, settings=settings)
    return DevTokenResponse(token=token)


@router.post("/dev/logout", response_model=DevLogoutResponse)
async def logout_dev_token(
    response: Response,
    settings: Settings = Depends(get_settings),
) -> DevLogoutResponse:
    if not settings.dev_token_enabled:
        raise AppError(404, "not_found", "dev token endpoint disabled")
    response.delete_cookie(
        key=DEV_TOKEN_COOKIE,
        path="/",
        samesite="lax",
        secure=settings.cookie_secure,
        httponly=True,
    )
    return DevLogoutResponse()


@router.get("/dev/session", response_model=DevSessionResponse)
async def get_dev_session(
    authorization: Annotated[str | None, Header()] = None,
    dev_token: Annotated[str | None, Cookie(alias=DEV_TOKEN_COOKIE)] = None,
    settings: Settings = Depends(get_settings),
) -> DevSessionResponse:
    """Report whether the browser has a valid JWT (cookie or Bearer)."""
    if not settings.dev_token_enabled:
        raise AppError(404, "not_found", "dev token endpoint disabled")
    token = extract_bearer_or_cookie(authorization, dev_token)
    if not token:
        return DevSessionResponse(authenticated=False)
    try:
        principal = decode_token(token, settings)
    except AppError:
        return DevSessionResponse(authenticated=False)
    return DevSessionResponse(
        authenticated=True,
        sub=principal.sub,
        org_id=principal.org_id,
        role=principal.role,
    )
