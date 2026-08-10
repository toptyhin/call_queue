from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from app.config import Settings
from app.db import db

log = structlog.get_logger(__name__)


async def crm_poller_loop(settings: Settings, stop: asyncio.Event) -> None:
    backoff_base = 2.0
    async with httpx.AsyncClient(timeout=5.0) as client:
        while not stop.is_set():
            try:
                await _poll_once(client, settings, backoff_base)
            except Exception:  # noqa: BLE001
                log.exception("crm_poller.error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.crm_poll_interval_sec)
            except TimeoutError:
                pass


async def _poll_once(
    client: httpx.AsyncClient, settings: Settings, backoff_base: float
) -> None:
    assert db.webhook_pool is not None
    async with db.webhook_pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, attempt_id, status, outcome, attempts
                FROM crm_outbox
                WHERE delivered_at IS NULL
                  AND next_attempt_at <= now()
                ORDER BY next_attempt_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            if row is None:
                return

            body = {
                "attempt_id": str(row["attempt_id"]),
                "status": row["status"],
                "outcome": row["outcome"],
            }
            try:
                resp = await client.post(settings.crm_url, json=body)
                if 200 <= resp.status_code < 300:
                    await conn.execute(
                        """
                        UPDATE crm_outbox
                        SET delivered_at = now(), last_error = NULL
                        WHERE id = $1
                        """,
                        row["id"],
                    )
                    log.info(
                        "crm_poller.delivered",
                        attempt_id=str(row["attempt_id"]),
                        outbox_id=str(row["id"]),
                    )
                    return
                # Persist a short code for the UI; body text stays in logs.
                err = f"HTTP {resp.status_code}"
                detail = resp.text[:200]
            except Exception as exc:  # noqa: BLE001
                err = "crm request failed"
                detail = str(exc)

            attempts = int(row["attempts"]) + 1
            delay = min(300.0, backoff_base**attempts)
            next_at = datetime.now(UTC) + timedelta(seconds=delay)
            await conn.execute(
                """
                UPDATE crm_outbox
                SET attempts = $2, last_error = $3, next_attempt_at = $4
                WHERE id = $1
                """,
                row["id"],
                attempts,
                err,
                next_at,
            )
            log.warning(
                "crm_poller.retry",
                attempt_id=str(row["attempt_id"]),
                attempts=attempts,
                error=err,
                detail=detail,
            )
