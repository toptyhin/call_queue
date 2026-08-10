from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

import jwt
from fastapi import Depends, Header

from app.config import Settings, get_settings
from app.errors import AppError

Role = Literal["worker", "authenticated"]


@dataclass(frozen=True, slots=True)
class Principal:
    sub: str
    org_id: UUID
    role: Role


def decode_token(token: str, settings: Settings) -> Principal:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["sub", "org_id", "role"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppError(401, "token_expired", "token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AppError(401, "unauthorized", "invalid token") from exc

    role = payload.get("role")
    if role not in ("worker", "authenticated"):
        raise AppError(401, "unauthorized", "invalid role claim")
    try:
        org_id = UUID(str(payload["org_id"]))
    except (ValueError, TypeError, KeyError) as exc:
        raise AppError(401, "unauthorized", "invalid org_id claim") from exc
    sub = str(payload.get("sub") or "")
    if not sub:
        raise AppError(401, "unauthorized", "invalid sub claim")
    return Principal(sub=sub, org_id=org_id, role=role)  # type: ignore[arg-type]


async def get_principal(
    authorization: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError(401, "unauthorized", "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise AppError(401, "unauthorized", "missing bearer token")
    return decode_token(token, settings)


def require_roles(*roles: Role):
    async def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in roles:
            raise AppError(403, "forbidden", "role not allowed")
        return principal

    return _dep
