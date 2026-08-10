from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import jwt
import orjson
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:54329/postgres")
os.environ.setdefault("APP_USER_DSN", "postgresql://app_user:app_user@localhost:54329/postgres")
os.environ.setdefault(
    "APP_WEBHOOK_DSN", "postgresql://app_webhook:app_webhook@localhost:54329/postgres"
)
os.environ.setdefault("WEBHOOK_SECRET", "dev-webhook-secret")
os.environ.setdefault("JWT_SECRET", "dev-jwt-secret-change-me-32bytes!!")
os.environ.setdefault("CRM_URL", "http://127.0.0.1:9/crm")
os.environ.setdefault("PROVIDER_URL", "http://127.0.0.1:9")
os.environ.setdefault("DEV_TOKEN_ENABLED", "true")

from app.config import get_settings
from app.db import db
from app.migrations import apply_migrations

ORG_A = UUID("11111111-1111-4111-8111-111111111111")
ORG_B = UUID("22222222-2222-4222-8222-222222222222")

_migrations_done = False


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    global _migrations_done
    settings = get_settings()
    if not _migrations_done:
        await apply_migrations(settings)
        _migrations_done = True

    # Recreate pools on the current event loop for each test.
    if db.app_pool is not None:
        await db.disconnect()
    await db.connect(settings)

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await db.disconnect()


@pytest_asyncio.fixture
async def admin_conn() -> AsyncIterator[asyncpg.Connection]:
    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    try:
        yield conn
    finally:
        await conn.close()


def make_token(org_id: UUID, role: str = "worker", sub: str = "tester") -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": sub,
            "org_id": str(org_id),
            "role": role,
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def sign_body(raw: bytes) -> str:
    settings = get_settings()
    digest = hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    return "sha256=" + digest


async def seed_org_campaign(
    conn: asyncpg.Connection,
    *,
    org_id: UUID,
    campaign_id: UUID | None = None,
    status: str = "active",
) -> UUID:
    campaign_id = campaign_id or uuid4()
    await conn.execute(
        "INSERT INTO orgs (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        org_id,
        f"org-{org_id}",
    )
    await conn.execute(
        """
        INSERT INTO campaigns (id, org_id, name, status)
        VALUES ($1, $2, 'c', $3)
        ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status
        """,
        campaign_id,
        org_id,
        status,
    )
    return campaign_id


async def seed_contact(
    conn: asyncpg.Connection,
    *,
    org_id: UUID,
    campaign_id: UUID,
    phone: str | None = None,
    timezone_name: str | None = None,
) -> UUID:
    contact_id = uuid4()
    phone = phone or f"+7900{contact_id.int % 10_000_000:07d}"
    if timezone_name is None:
        utc_hour = datetime.now(UTC).hour
        offset = (12 - utc_hour) % 24
        if offset == 0:
            tz = "UTC"
        elif offset <= 12:
            tz = f"Etc/GMT-{offset}"
        else:
            tz = f"Etc/GMT+{24 - offset}"
    else:
        tz = timezone_name
    await conn.execute(
        """
        INSERT INTO contacts (
            id, org_id, campaign_id, phone_e164, timezone, attempts_count, do_not_call
        ) VALUES ($1, $2, $3, $4, $5, 0, false)
        """,
        contact_id,
        org_id,
        campaign_id,
        phone,
        tz,
    )
    return contact_id


def webhook_payload(
    *,
    event_id: str | None = None,
    call_id: str = "call_test",
    sequence: int = 1,
    type_: str = "call.dialing",
    data: dict[str, Any] | None = None,
) -> bytes:
    body = {
        "event_id": event_id or f"evt_{uuid4().hex}",
        "call_id": call_id,
        "sequence": sequence,
        "type": type_,
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "data": data or {},
    }
    return orjson.dumps(body)
