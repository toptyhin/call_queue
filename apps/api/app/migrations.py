"""Apply numbered SQL migrations before the HTTP server opens the port."""

from __future__ import annotations

import re
from pathlib import Path

import asyncpg
import structlog

from app.config import Settings

log = structlog.get_logger(__name__)

MIGRATION_RE = re.compile(r"^(\d+)_.*\.sql$")


async def apply_migrations(settings: Settings) -> None:
    migrations_dir = Path(settings.migrations_dir)
    if not migrations_dir.is_absolute():
        # Resolve relative to apps/api package root (parent of app/)
        migrations_dir = Path(__file__).resolve().parent.parent / migrations_dir

    files = sorted(
        (p for p in migrations_dir.glob("*.sql") if MIGRATION_RE.match(p.name)),
        key=lambda p: int(MIGRATION_RE.match(p.name).group(1)),  # type: ignore[union-attr]
    )

    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            row["version"]
            for row in await conn.fetch("SELECT version FROM schema_migrations")
        }

        for path in files:
            version = path.name
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            # Substitute role passwords from env (migrations create roles).
            sql = sql.replace("__APP_USER_PASSWORD__", settings.app_user_password)
            sql = sql.replace("__APP_WEBHOOK_PASSWORD__", settings.app_webhook_password)
            log.info("migration.apply", version=version)
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES ($1)",
                    version,
                )
            log.info("migration.applied", version=version)
    finally:
        await conn.close()
