"""Connection pools and tenant-scoped transactions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from app.config import Settings

log = structlog.get_logger(__name__)


class Database:
    def __init__(self) -> None:
        self.app_pool: asyncpg.Pool | None = None
        self.webhook_pool: asyncpg.Pool | None = None
        self.admin_pool: asyncpg.Pool | None = None

    async def connect(self, settings: Settings) -> None:
        self.admin_pool = await asyncpg.create_pool(
            settings.database_url, min_size=1, max_size=4
        )
        self.app_pool = await asyncpg.create_pool(
            settings.app_user_dsn, min_size=2, max_size=20
        )
        self.webhook_pool = await asyncpg.create_pool(
            settings.app_webhook_dsn, min_size=2, max_size=20
        )
        log.info("db.pools_ready")

    async def disconnect(self) -> None:
        for pool in (self.app_pool, self.webhook_pool, self.admin_pool):
            if pool is not None:
                await pool.close()
        self.app_pool = self.webhook_pool = self.admin_pool = None

    @asynccontextmanager
    async def tenant_connection(self, org_id: UUID) -> AsyncIterator[asyncpg.Connection]:
        assert self.app_pool is not None
        async with self.app_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.org_id', $1, true)",
                    str(org_id),
                )
                yield conn

    @asynccontextmanager
    async def webhook_connection(self) -> AsyncIterator[asyncpg.Connection]:
        assert self.webhook_pool is not None
        async with self.webhook_pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    @asynccontextmanager
    async def admin_connection(self) -> AsyncIterator[asyncpg.Connection]:
        assert self.admin_pool is not None
        async with self.admin_pool.acquire() as conn:
            async with conn.transaction():
                yield conn


db = Database()


async def fetchrow_mapping(
    conn: asyncpg.Connection, query: str, *args: Any
) -> dict[str, Any] | None:
    row = await conn.fetchrow(query, *args)
    return dict(row) if row is not None else None
