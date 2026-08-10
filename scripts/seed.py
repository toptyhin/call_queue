"""Seed demo org/campaign/contacts + a completed attempt with transcript.

Run inside API container: `python -m scripts.seed` or `make seed`.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import asyncpg
import orjson

# Allow `python scripts/seed.py` from repo root / container.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
CAMPAIGN_ID = UUID("00000000-0000-4000-8000-000000000010")
CONTACT_ID = UUID("00000000-0000-4000-8000-000000000020")
ATTEMPT_ID = UUID("00000000-0000-4000-8000-000000000030")


async def main() -> None:
    dsn = os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:54329/postgres"
    )
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO orgs (id, name) VALUES ($1, 'Demo Org')
            ON CONFLICT (id) DO NOTHING
            """,
            ORG_ID,
        )
        await conn.execute(
            """
            INSERT INTO campaigns (id, org_id, name, status)
            VALUES ($1, $2, 'Demo Campaign', 'active')
            ON CONFLICT (id) DO UPDATE SET status = 'active'
            """,
            CAMPAIGN_ID,
            ORG_ID,
        )
        # Contacts in a daytime-friendly timezone (Europe/Moscow covers most hours).
        phones = [
            ("+79001110001", "Europe/Moscow"),
            ("+79001110002", "Europe/Moscow"),
            ("+79001110003", "UTC"),
            ("+79001110004", "Asia/Tokyo"),
            ("+79001110005", "America/New_York"),
        ]
        for i, (phone, tz) in enumerate(phones):
            cid = UUID(f"00000000-0000-4000-8000-00000000002{i}")
            await conn.execute(
                """
                INSERT INTO contacts (
                    id, org_id, campaign_id, phone_e164, timezone,
                    attempts_count, do_not_call
                )
                VALUES ($1, $2, $3, $4, $5, 0, false)
                ON CONFLICT (id) DO NOTHING
                """,
                cid,
                ORG_ID,
                CAMPAIGN_ID,
                phone,
                tz,
            )

        # Completed attempt with transcript for analysis UI.
        await conn.execute(
            """
            INSERT INTO contacts (
                id, org_id, campaign_id, phone_e164, timezone,
                attempts_count, last_attempt_at, do_not_call
            )
            VALUES ($1, $2, $3, '+79001119999', 'Europe/Moscow', 1, now(), false)
            ON CONFLICT (id) DO NOTHING
            """,
            CONTACT_ID,
            ORG_ID,
            CAMPAIGN_ID,
        )
        transcript = (
            "Менеджер: Здравствуйте! Расскажу про тарифы.\n"
            "Клиент: Интересно, но дорого.\n"
            "Менеджер: Могу предложить скидку.\n"
            "Клиент: Пришлите предложение."
        )
        await conn.execute(
            """
            INSERT INTO call_attempts (
                id, org_id, campaign_id, contact_id, provider_call_id,
                status, started_at, ended_at, outcome, transcript,
                last_applied_sequence
            )
            VALUES (
                $1, $2, $3, $4, 'call_seed_completed',
                'completed', now() - interval '10 minutes', now() - interval '5 minutes',
                $5::jsonb, $6, 3
            )
            ON CONFLICT (id) DO UPDATE
            SET transcript = EXCLUDED.transcript, status = 'completed'
            """,
            ATTEMPT_ID,
            ORG_ID,
            CAMPAIGN_ID,
            CONTACT_ID,
            orjson.dumps({"sip_code": 200, "duration_sec": 120}).decode(),
            transcript,
        )
        print(
            orjson.dumps(
                {
                    "org_id": str(ORG_ID),
                    "campaign_id": str(CAMPAIGN_ID),
                    "completed_attempt_id": str(ATTEMPT_ID),
                    "seeded_at": datetime.now(timezone.utc).isoformat(),
                }
            ).decode()
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
