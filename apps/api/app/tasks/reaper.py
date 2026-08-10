from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import orjson
import structlog

from app.config import Settings
from app.db import db

log = structlog.get_logger(__name__)


async def reaper_loop(settings: Settings, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await _reap_once(settings)
        except Exception:  # noqa: BLE001
            log.exception("reaper.error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.reaper_interval_sec)
        except asyncio.TimeoutError:
            pass


async def _reap_once(settings: Settings) -> None:
    assert db.webhook_pool is not None
    outcome = orjson.dumps({"reason": "stale_timeout"}).decode()
    async with db.webhook_pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                UPDATE call_attempts
                SET status = 'failed',
                    ended_at = now(),
                    outcome = $1::jsonb
                WHERE status IN ('queued', 'dialing', 'in_progress')
                  AND created_at <= now() - make_interval(mins => $2)
                RETURNING id
                """,
                outcome,
                settings.stale_attempt_minutes,
            )
    if rows:
        log.info("reaper.stale", count=len(rows), ids=[str(r["id"]) for r in rows])
