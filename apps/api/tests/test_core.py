from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient

from tests.conftest import (
    ORG_A,
    ORG_B,
    make_token,
    seed_contact,
    seed_org_campaign,
    sign_body,
    webhook_payload,
)


@pytest.mark.asyncio
async def test_healthz(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "x-request-id" in r.headers


@pytest.mark.asyncio
async def test_claim_and_lock(client: AsyncClient, admin_conn) -> None:
    campaign_id = await seed_org_campaign(admin_conn, org_id=ORG_A)
    await seed_contact(admin_conn, org_id=ORG_A, campaign_id=campaign_id)
    token = make_token(ORG_A, "worker")

    r1 = await client.post(
        "/rpc/claim_next_contact",
        headers={"Authorization": f"Bearer {token}"},
        json={"campaign_id": str(campaign_id)},
    )
    assert r1.status_code == 200
    contact = r1.json()["contact"]
    assert contact is not None
    assert contact["attempt_id"]

    r2 = await client.post(
        "/rpc/claim_next_contact",
        headers={"Authorization": f"Bearer {token}"},
        json={"campaign_id": str(campaign_id)},
    )
    assert r2.status_code == 200
    assert r2.json()["contact"] is None


@pytest.mark.asyncio
async def test_claim_foreign_campaign_404(client: AsyncClient, admin_conn) -> None:
    campaign_b = await seed_org_campaign(admin_conn, org_id=ORG_B)
    await seed_contact(admin_conn, org_id=ORG_B, campaign_id=campaign_b)
    token_a = make_token(ORG_A, "worker")
    r = await client.post(
        "/rpc/claim_next_contact",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"campaign_id": str(campaign_b)},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


@pytest.mark.asyncio
async def test_webhook_invalid_signature(client: AsyncClient, admin_conn) -> None:
    raw = webhook_payload()
    before = await admin_conn.fetchval("SELECT count(*) FROM webhook_events")
    r = await client.post(
        "/webhooks/calls",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Signature": "sha256=" + ("00" * 32),
        },
    )
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_signature"
    after = await admin_conn.fetchval("SELECT count(*) FROM webhook_events")
    assert after == before


@pytest.mark.asyncio
async def test_webhook_dedup_and_apply(client: AsyncClient, admin_conn) -> None:
    campaign_id = await seed_org_campaign(admin_conn, org_id=ORG_A)
    await seed_contact(admin_conn, org_id=ORG_A, campaign_id=campaign_id)
    token = make_token(ORG_A, "worker")
    claimed = await client.post(
        "/rpc/claim_next_contact",
        headers={"Authorization": f"Bearer {token}"},
        json={"campaign_id": str(campaign_id)},
    )
    attempt_id = claimed.json()["contact"]["attempt_id"]
    call_id = f"call_{uuid4().hex[:12]}"

    link = await client.post(
        f"/api/call_attempts/{attempt_id}/provider-link",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider_call_id": call_id},
    )
    assert link.status_code == 200

    raw = webhook_payload(call_id=call_id, sequence=1, type_="call.dialing")
    sig = sign_body(raw)
    r1 = await client.post(
        "/webhooks/calls",
        content=raw,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert r1.status_code == 200
    r2 = await client.post(
        "/webhooks/calls",
        content=raw,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert r2.status_code == 200
    status = await admin_conn.fetchval(
        "SELECT status FROM call_attempts WHERE id = $1", attempt_id
    )
    assert status == "dialing"
    cnt = await admin_conn.fetchval(
        "SELECT count(*) FROM webhook_events WHERE provider_call_id = $1", call_id
    )
    assert cnt == 1


@pytest.mark.asyncio
async def test_webhook_buffer_then_link(client: AsyncClient, admin_conn) -> None:
    campaign_id = await seed_org_campaign(admin_conn, org_id=ORG_A)
    await seed_contact(admin_conn, org_id=ORG_A, campaign_id=campaign_id)
    token = make_token(ORG_A, "worker")
    claimed = await client.post(
        "/rpc/claim_next_contact",
        headers={"Authorization": f"Bearer {token}"},
        json={"campaign_id": str(campaign_id)},
    )
    attempt_id = claimed.json()["contact"]["attempt_id"]
    call_id = f"call_{uuid4().hex[:12]}"

    raw = webhook_payload(call_id=call_id, sequence=1, type_="call.answered")
    r = await client.post(
        "/webhooks/calls",
        content=raw,
        headers={"Content-Type": "application/json", "X-Signature": sign_body(raw)},
    )
    assert r.status_code == 200
    status = await admin_conn.fetchval(
        "SELECT status FROM call_attempts WHERE id = $1", attempt_id
    )
    assert status == "queued"

    link = await client.post(
        f"/api/call_attempts/{attempt_id}/provider-link",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider_call_id": call_id},
    )
    assert link.status_code == 200
    status = await admin_conn.fetchval(
        "SELECT status FROM call_attempts WHERE id = $1", attempt_id
    )
    assert status == "in_progress"


@pytest.mark.asyncio
async def test_terminal_trigger_and_outbox(client: AsyncClient, admin_conn) -> None:
    campaign_id = await seed_org_campaign(admin_conn, org_id=ORG_A)
    contact_id = await seed_contact(admin_conn, org_id=ORG_A, campaign_id=campaign_id)
    token = make_token(ORG_A, "worker")
    claimed = await client.post(
        "/rpc/claim_next_contact",
        headers={"Authorization": f"Bearer {token}"},
        json={"campaign_id": str(campaign_id)},
    )
    attempt_id = claimed.json()["contact"]["attempt_id"]
    call_id = f"call_{uuid4().hex[:12]}"
    await client.post(
        f"/api/call_attempts/{attempt_id}/provider-link",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider_call_id": call_id},
    )

    raw = webhook_payload(
        call_id=call_id,
        sequence=5,
        type_="call.completed",
        data={"do_not_call": True, "transcript": "hello world", "duration_sec": 10},
    )
    r = await client.post(
        "/webhooks/calls",
        content=raw,
        headers={"Content-Type": "application/json", "X-Signature": sign_body(raw)},
    )
    assert r.status_code == 200

    row = await admin_conn.fetchrow(
        "SELECT status, transcript FROM call_attempts WHERE id = $1", attempt_id
    )
    assert row["status"] == "completed"
    assert row["transcript"] == "hello world"

    contact = await admin_conn.fetchrow(
        "SELECT attempts_count, do_not_call, locked_attempt_id FROM contacts WHERE id = $1",
        contact_id,
    )
    assert contact["attempts_count"] == 1
    assert contact["do_not_call"] is True
    assert contact["locked_attempt_id"] is None

    outbox = await admin_conn.fetchval(
        "SELECT count(*) FROM crm_outbox WHERE attempt_id = $1", attempt_id
    )
    assert outbox == 1


@pytest.mark.asyncio
async def test_sequence_and_terminal_guards(client: AsyncClient, admin_conn) -> None:
    campaign_id = await seed_org_campaign(admin_conn, org_id=ORG_A)
    await seed_contact(admin_conn, org_id=ORG_A, campaign_id=campaign_id)
    token = make_token(ORG_A, "worker")
    claimed = await client.post(
        "/rpc/claim_next_contact",
        headers={"Authorization": f"Bearer {token}"},
        json={"campaign_id": str(campaign_id)},
    )
    attempt_id = claimed.json()["contact"]["attempt_id"]
    call_id = f"call_{uuid4().hex[:12]}"
    await client.post(
        f"/api/call_attempts/{attempt_id}/provider-link",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider_call_id": call_id},
    )

    for seq, typ in [(2, "call.answered"), (1, "call.dialing"), (3, "call.completed")]:
        raw = webhook_payload(
            call_id=call_id,
            sequence=seq,
            type_=typ,
            data={"transcript": "t"} if typ == "call.completed" else {},
        )
        await client.post(
            "/webhooks/calls",
            content=raw,
            headers={"Content-Type": "application/json", "X-Signature": sign_body(raw)},
        )

    status = await admin_conn.fetchval(
        "SELECT status FROM call_attempts WHERE id = $1", attempt_id
    )
    assert status == "completed"

    # Late event after terminal — saved, not applied
    raw = webhook_payload(call_id=call_id, sequence=4, type_="call.dialing")
    await client.post(
        "/webhooks/calls",
        content=raw,
        headers={"Content-Type": "application/json", "X-Signature": sign_body(raw)},
    )
    status = await admin_conn.fetchval(
        "SELECT status FROM call_attempts WHERE id = $1", attempt_id
    )
    assert status == "completed"


@pytest.mark.asyncio
async def test_abort_role_and_provider_link_idempotent(
    client: AsyncClient, admin_conn
) -> None:
    campaign_id = await seed_org_campaign(admin_conn, org_id=ORG_A)
    await seed_contact(admin_conn, org_id=ORG_A, campaign_id=campaign_id)
    worker = make_token(ORG_A, "worker")
    user = make_token(ORG_A, "authenticated")
    claimed = await client.post(
        "/rpc/claim_next_contact",
        headers={"Authorization": f"Bearer {worker}"},
        json={"campaign_id": str(campaign_id)},
    )
    attempt_id = claimed.json()["contact"]["attempt_id"]

    forbidden = await client.post(
        f"/api/call_attempts/{attempt_id}/abort",
        headers={"Authorization": f"Bearer {user}"},
        json={"reason": "x"},
    )
    assert forbidden.status_code == 403

    call_id = f"call_{uuid4().hex[:12]}"
    r1 = await client.post(
        f"/api/call_attempts/{attempt_id}/provider-link",
        headers={"Authorization": f"Bearer {worker}"},
        json={"provider_call_id": call_id},
    )
    r2 = await client.post(
        f"/api/call_attempts/{attempt_id}/provider-link",
        headers={"Authorization": f"Bearer {worker}"},
        json={"provider_call_id": call_id},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    r3 = await client.post(
        f"/api/call_attempts/{attempt_id}/provider-link",
        headers={"Authorization": f"Bearer {worker}"},
        json={"provider_call_id": call_id + "_other"},
    )
    assert r3.status_code == 409
    assert r3.json()["code"] == "already_linked"
