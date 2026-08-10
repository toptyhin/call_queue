"""Read-API for call attempts: list, detail, roles, org isolation."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from tests.conftest import (
    make_token,
    seed_contact,
    seed_org_campaign,
    sign_body,
    webhook_payload,
)


async def _claim_attempt(
    client: AsyncClient, *, org_id: UUID, campaign_id: UUID
) -> str:
    token = make_token(org_id, "worker")
    r = await client.post(
        "/rpc/claim_next_contact",
        headers={"Authorization": f"Bearer {token}"},
        json={"campaign_id": str(campaign_id)},
    )
    assert r.status_code == 200
    contact = r.json()["contact"]
    assert contact is not None
    return contact["attempt_id"]


async def _complete_attempt(
    client: AsyncClient,
    *,
    org_id: UUID,
    attempt_id: str,
    transcript: str = "hello from test",
) -> str:
    """Link provider_call_id and apply a completed webhook; return call_id."""
    worker = make_token(org_id, "worker")
    call_id = f"call_{uuid4().hex[:12]}"
    link = await client.post(
        f"/api/call_attempts/{attempt_id}/provider-link",
        headers={"Authorization": f"Bearer {worker}"},
        json={"provider_call_id": call_id},
    )
    assert link.status_code == 200
    raw = webhook_payload(
        call_id=call_id,
        sequence=1,
        type_="call.completed",
        data={"transcript": transcript, "duration_sec": 12},
    )
    wh = await client.post(
        "/webhooks/calls",
        content=raw,
        headers={"Content-Type": "application/json", "X-Signature": sign_body(raw)},
    )
    assert wh.status_code == 200
    return call_id


def _fresh_org() -> UUID:
    """Per-test org so list assertions are not polluted by leftover rows."""
    return uuid4()


@pytest.mark.asyncio
async def test_list_empty(client: AsyncClient, admin_conn) -> None:
    org_id = _fresh_org()
    await seed_org_campaign(admin_conn, org_id=org_id)
    token = make_token(org_id, "authenticated")
    r = await client.get(
        "/api/call_attempts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_requires_authenticated_role(
    client: AsyncClient, admin_conn
) -> None:
    org_id = _fresh_org()
    await seed_org_campaign(admin_conn, org_id=org_id)
    missing = await client.get("/api/call_attempts")
    assert missing.status_code == 401
    assert missing.json()["code"] == "unauthorized"

    worker = make_token(org_id, "worker")
    forbidden = await client.get(
        "/api/call_attempts",
        headers={"Authorization": f"Bearer {worker}"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "forbidden"


@pytest.mark.asyncio
async def test_list_fields_and_cursor_pagination(
    client: AsyncClient, admin_conn
) -> None:
    org_id = _fresh_org()
    campaign_id = await seed_org_campaign(admin_conn, org_id=org_id)
    await seed_contact(admin_conn, org_id=org_id, campaign_id=campaign_id)
    await seed_contact(admin_conn, org_id=org_id, campaign_id=campaign_id)
    a1 = await _claim_attempt(client, org_id=org_id, campaign_id=campaign_id)
    a2 = await _claim_attempt(client, org_id=org_id, campaign_id=campaign_id)
    await _complete_attempt(client, org_id=org_id, attempt_id=a1, transcript="t1")
    await _complete_attempt(client, org_id=org_id, attempt_id=a2, transcript="t2")

    token = make_token(org_id, "authenticated")
    page1 = await client.get(
        "/api/call_attempts",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": 1},
    )
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 1
    assert body1["next_cursor"]
    item = body1["items"][0]
    assert set(item) >= {
        "id",
        "status",
        "phone",
        "campaign_name",
        "started_at",
        "ended_at",
        "created_at",
    }
    assert item["status"] == "completed"
    assert item["campaign_name"] == "c"
    assert item["phone"].startswith("+")
    assert item["id"] in {a1, a2}

    page2 = await client.get(
        "/api/call_attempts",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": 1, "cursor": body1["next_cursor"]},
    )
    assert page2.status_code == 200
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["items"][0]["id"] != item["id"]
    assert {body1["items"][0]["id"], body2["items"][0]["id"]} == {a1, a2}


@pytest.mark.asyncio
async def test_list_invalid_cursor(client: AsyncClient, admin_conn) -> None:
    org_id = _fresh_org()
    await seed_org_campaign(admin_conn, org_id=org_id)
    token = make_token(org_id, "authenticated")
    r = await client.get(
        "/api/call_attempts",
        headers={"Authorization": f"Bearer {token}"},
        params={"cursor": "not-a-valid-cursor!!!"},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


@pytest.mark.asyncio
async def test_detail_history_and_crm(client: AsyncClient, admin_conn) -> None:
    org_id = _fresh_org()
    campaign_id = await seed_org_campaign(admin_conn, org_id=org_id)
    await seed_contact(admin_conn, org_id=org_id, campaign_id=campaign_id)
    attempt_id = await _claim_attempt(client, org_id=org_id, campaign_id=campaign_id)
    call_id = await _complete_attempt(
        client, org_id=org_id, attempt_id=attempt_id, transcript="detail transcript"
    )

    token = make_token(org_id, "authenticated")
    r = await client.get(
        f"/api/call_attempts/{attempt_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    detail = r.json()
    assert detail["id"] == attempt_id
    assert detail["status"] == "completed"
    assert detail["provider_call_id"] == call_id
    assert detail["transcript"] == "detail transcript"
    assert detail["campaign_name"] == "c"
    assert detail["contact"]["phone"].startswith("+")
    assert "timezone" in detail["contact"]

    history = detail["status_history"]
    assert history[0]["status"] == "queued"
    assert history[0]["source"] == "claim"
    webhook_steps = [h for h in history if h["source"] == "webhook"]
    assert any(h["status"] == "completed" for h in webhook_steps)

    assert detail["crm"] is not None
    assert detail["crm"]["state"] in ("pending", "retrying", "delivered")
    assert detail["crm"]["attempts"] >= 0
    assert isinstance(detail["analyses"], list)


@pytest.mark.asyncio
async def test_detail_foreign_org_isolated(
    client: AsyncClient, admin_conn
) -> None:
    org_a = _fresh_org()
    org_b = _fresh_org()
    await seed_org_campaign(admin_conn, org_id=org_a)
    campaign_b = await seed_org_campaign(admin_conn, org_id=org_b)
    await seed_contact(admin_conn, org_id=org_b, campaign_id=campaign_b)
    attempt_b = await _claim_attempt(client, org_id=org_b, campaign_id=campaign_b)

    token_a = make_token(org_a, "authenticated")
    r = await client.get(
        f"/api/call_attempts/{attempt_b}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


@pytest.mark.asyncio
async def test_detail_not_found(client: AsyncClient, admin_conn) -> None:
    org_id = _fresh_org()
    await seed_org_campaign(admin_conn, org_id=org_id)
    token = make_token(org_id, "authenticated")
    missing_id = uuid4()
    r = await client.get(
        f"/api/call_attempts/{missing_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


@pytest.mark.asyncio
async def test_detail_requires_authenticated_role(
    client: AsyncClient, admin_conn
) -> None:
    org_id = _fresh_org()
    campaign_id = await seed_org_campaign(admin_conn, org_id=org_id)
    await seed_contact(admin_conn, org_id=org_id, campaign_id=campaign_id)
    attempt_id = await _claim_attempt(client, org_id=org_id, campaign_id=campaign_id)

    missing = await client.get(f"/api/call_attempts/{attempt_id}")
    assert missing.status_code == 401

    worker = make_token(org_id, "worker")
    forbidden = await client.get(
        f"/api/call_attempts/{attempt_id}",
        headers={"Authorization": f"Bearer {worker}"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "forbidden"


@pytest.mark.asyncio
async def test_list_hides_foreign_org_attempts(
    client: AsyncClient, admin_conn
) -> None:
    org_a = _fresh_org()
    org_b = _fresh_org()
    campaign_a = await seed_org_campaign(admin_conn, org_id=org_a)
    campaign_b = await seed_org_campaign(admin_conn, org_id=org_b)
    await seed_contact(admin_conn, org_id=org_a, campaign_id=campaign_a)
    await seed_contact(admin_conn, org_id=org_b, campaign_id=campaign_b)
    attempt_a = await _claim_attempt(client, org_id=org_a, campaign_id=campaign_a)
    attempt_b = await _claim_attempt(client, org_id=org_b, campaign_id=campaign_b)

    token_a = make_token(org_a, "authenticated")
    r = await client.get(
        "/api/call_attempts",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["items"]}
    assert attempt_a in ids
    assert attempt_b not in ids
