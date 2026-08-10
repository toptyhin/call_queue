"""Seed N contacts for claim load testing (default 2_000_000)."""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
CAMPAIGN_ID = UUID("00000000-0000-4000-8000-000000000010")


def daytime_tz() -> str:
    """Pick an Etc/GMT* zone where local wall time is ~12:00."""
    utc_hour = datetime.now(timezone.utc).hour
    offset = (12 - utc_hour) % 24
    if offset == 0:
        return "UTC"
    if offset <= 14:
        # Etc/GMT-N == UTC+N
        return f"Etc/GMT-{offset}"
    # Etc/GMT+N == UTC-N
    return f"Etc/GMT+{24 - offset}"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=2_000_000)
    parser.add_argument("--batch", type=int, default=50_000)
    args = parser.parse_args()

    dsn = os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:54329/postgres"
    )
    tz = daytime_tz()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO orgs (id, name) VALUES ($1, 'Load Org') ON CONFLICT DO NOTHING",
            ORG_ID,
        )
        await conn.execute(
            """
            INSERT INTO campaigns (id, org_id, name, status)
            VALUES ($1, $2, 'Load Campaign', 'active')
            ON CONFLICT (id) DO UPDATE SET status = 'active'
            """,
            CAMPAIGN_ID,
            ORG_ID,
        )

        await conn.execute(
            """
            DELETE FROM call_attempts
            WHERE campaign_id = $1
              AND contact_id IN (
                SELECT id FROM contacts
                WHERE campaign_id = $1
                  AND id::text NOT LIKE '00000000-0000-4000-8000-%'
              )
            """,
            CAMPAIGN_ID,
        )
        await conn.execute(
            """
            DELETE FROM contacts
            WHERE campaign_id = $1
              AND id::text NOT LIKE '00000000-0000-4000-8000-%'
            """,
            CAMPAIGN_ID,
        )

        remaining = args.count
        inserted = 0
        t0 = time.perf_counter()
        phone_base = 79000000000
        while remaining > 0:
            n = min(args.batch, remaining)
            await conn.execute(
                """
                INSERT INTO contacts (
                    org_id, campaign_id, phone_e164, timezone, attempts_count, do_not_call
                )
                SELECT
                    $1,
                    $2,
                    '+' || ($4::bigint + g)::text,
                    $5,
                    0,
                    false
                FROM generate_series(1, $3) AS g
                """,
                ORG_ID,
                CAMPAIGN_ID,
                n,
                phone_base + inserted,
                tz,
            )
            remaining -= n
            inserted += n
            elapsed = time.perf_counter() - t0
            print(f"inserted={inserted} tz={tz} elapsed_s={elapsed:.1f}", flush=True)
        print(f"done count={inserted}", flush=True)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
