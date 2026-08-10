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
CONTACT_ID = UUID("00000000-0000-4000-8000-000000000029")
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
            ON CONFLICT (id) DO UPDATE
            SET phone_e164 = EXCLUDED.phone_e164,
                timezone = EXCLUDED.timezone,
                attempts_count = EXCLUDED.attempts_count,
                last_attempt_at = EXCLUDED.last_attempt_at
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
        outcome = {"sip_code": 200, "duration_sec": 120, "transcript": transcript}
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
                $5::jsonb, $6, 4
            )
            ON CONFLICT (id) DO UPDATE
            SET contact_id = EXCLUDED.contact_id,
                transcript = EXCLUDED.transcript,
                status = 'completed',
                outcome = EXCLUDED.outcome,
                provider_call_id = EXCLUDED.provider_call_id,
                last_applied_sequence = EXCLUDED.last_applied_sequence
            """,
            ATTEMPT_ID,
            ORG_ID,
            CAMPAIGN_ID,
            CONTACT_ID,
            orjson.dumps(outcome).decode(),
            transcript,
        )

        # Timeline + CRM demo data (INSERT does not fire terminal UPDATE trigger).
        now = datetime.now(timezone.utc)
        history = [
            ("seed_ev_queued", 1, "call.queued", {"attempt_id": str(ATTEMPT_ID)}),
            ("seed_ev_dialing", 2, "call.dialing", {"attempt_id": str(ATTEMPT_ID)}),
            ("seed_ev_answered", 3, "call.answered", {"attempt_id": str(ATTEMPT_ID)}),
            ("seed_ev_completed", 4, "call.completed", outcome),
        ]
        for event_id, seq, etype, data in history:
            payload = {
                "event_id": event_id,
                "call_id": "call_seed_completed",
                "sequence": seq,
                "type": etype,
                "occurred_at": now.isoformat().replace("+00:00", "Z"),
                "data": data,
            }
            await conn.execute(
                """
                INSERT INTO webhook_events (
                    provider_event_id, provider_call_id, sequence, type,
                    payload, applied_at, call_attempt_id
                )
                VALUES ($1, 'call_seed_completed', $2, $3, $4::jsonb, now(), $5)
                ON CONFLICT (provider_event_id) DO NOTHING
                """,
                event_id,
                seq,
                etype,
                orjson.dumps(payload).decode(),
                ATTEMPT_ID,
            )

        await conn.execute(
            """
            INSERT INTO crm_outbox (attempt_id, status, outcome, delivered_at, attempts)
            SELECT $1, 'completed', $2::jsonb, now(), 1
            WHERE NOT EXISTS (
                SELECT 1 FROM crm_outbox WHERE attempt_id = $1
            )
            """,
            ATTEMPT_ID,
            orjson.dumps(outcome).decode(),
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
