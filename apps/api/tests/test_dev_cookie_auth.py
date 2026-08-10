"""Dev token HttpOnly cookie + session endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import ORG_A


@pytest.mark.asyncio
async def test_mint_sets_httponly_cookie_and_session(client: AsyncClient) -> None:
    res = await client.post(
        "/dev/token",
        json={
            "sub": "cookie-user",
            "org_id": str(ORG_A),
            "role": "authenticated",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body.get("token"), str) and body["token"]

    assert "dev_token" in res.cookies
    set_cookie = res.headers.get("set-cookie") or ""
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower()

    session = await client.get("/dev/session")
    assert session.status_code == 200
    data = session.json()
    assert data["authenticated"] is True
    assert data["sub"] == "cookie-user"
    assert data["org_id"] == str(ORG_A)
    assert data["role"] == "authenticated"

    # Cookie alone authorizes tenant API (no Authorization header).
    listing = await client.get("/api/call_attempts", params={"limit": 1})
    assert listing.status_code == 200
    assert "items" in listing.json()


@pytest.mark.asyncio
async def test_logout_clears_cookie(client: AsyncClient) -> None:
    mint = await client.post(
        "/dev/token",
        json={
            "sub": "logout-user",
            "org_id": str(ORG_A),
            "role": "authenticated",
        },
    )
    assert mint.status_code == 200

    logout = await client.post("/dev/logout")
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}

    session = await client.get("/dev/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is False

    listing = await client.get("/api/call_attempts", params={"limit": 1})
    assert listing.status_code == 401
