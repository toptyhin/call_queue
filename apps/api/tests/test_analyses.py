from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient

from tests.conftest import ORG_A, make_token, seed_contact, seed_org_campaign


async def _completed_attempt_with_transcript(admin_conn, transcript: str = "hello") -> str:
    campaign_id = await seed_org_campaign(admin_conn, org_id=ORG_A)
    contact_id = await seed_contact(admin_conn, org_id=ORG_A, campaign_id=campaign_id)
    attempt_id = uuid4()
    await admin_conn.execute(
        """
        INSERT INTO call_attempts (
            id, org_id, campaign_id, contact_id, status, transcript,
            started_at, ended_at, outcome, last_applied_sequence
        ) VALUES (
            $1, $2, $3, $4, 'completed', $5,
            now() - interval '5 minutes', now() - interval '1 minute',
            '{"ok": true}'::jsonb, 1
        )
        """,
        attempt_id,
        ORG_A,
        campaign_id,
        contact_id,
        transcript,
    )
    return str(attempt_id)


@pytest.mark.asyncio
async def test_create_analysis_requires_completed_transcript(
    client: AsyncClient, admin_conn
) -> None:
    campaign_id = await seed_org_campaign(admin_conn, org_id=ORG_A)
    contact_id = await seed_contact(admin_conn, org_id=ORG_A, campaign_id=campaign_id)
    attempt_id = uuid4()
    await admin_conn.execute(
        """
        INSERT INTO call_attempts (id, org_id, campaign_id, contact_id, status)
        VALUES ($1, $2, $3, $4, 'queued')
        """,
        attempt_id,
        ORG_A,
        campaign_id,
        contact_id,
    )
    token = make_token(ORG_A, "authenticated")
    r = await client.post(
        "/api/analyses",
        headers={"Authorization": f"Bearer {token}"},
        json={"call_attempt_id": str(attempt_id)},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "attempt_not_completed"


@pytest.mark.asyncio
@pytest.mark.skipif(
    True,  # enabled in e2e against live mocks; unit suite keeps provider blackholed
    reason="requires live PROVIDER_URL mock",
)
async def test_analysis_stream_happy_path(client: AsyncClient, admin_conn) -> None:
    attempt_id = await _completed_attempt_with_transcript(admin_conn)
    token = make_token(ORG_A, "authenticated")
    created = await client.post(
        "/api/analyses",
        headers={"Authorization": f"Bearer {token}"},
        json={"call_attempt_id": attempt_id},
    )
    assert created.status_code == 200
    analysis_id = created.json()["id"]

    for _ in range(50):
        got = await client.get(
            f"/api/analyses/{analysis_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if got.json()["status"] == "done":
            break
        await asyncio.sleep(0.2)
    else:
        pytest.fail("analysis did not complete")

    body = got.json()
    assert body["result"]["summary"]
    assert body["result"]["lead_score"] == 62
