"""End-to-end scenario runner: contact -> call webhooks -> CRM -> LLM analysis.

Run against a live dev stand:

    docker compose --profile dev up -d
    make seed
    make e2e-flow            # ~1-2 minutes, chaos across telephony/CRM/LLM

The script claims fresh contacts from the seeded demo campaign and drives each
through a scenario. It asserts statuses end to end and exits non-zero on
failure. Chaos hooks come from the dev mocks:

- LLM provider: transcript marker CHAOS_429 / CHAOS_BREAK / CHAOS_INVALID.
- CRM: outcome.__fail_n (or POST /crm/_chaos {"fail_n": N}) arms N 500s then 200.

Requires: API on :8080, mocks on :8090 (see .env / compose defaults).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import orjson

# ---------------------------------------------------------------------------
# Config (env-overridable, defaults match .env.example / compose dev)
# ---------------------------------------------------------------------------

API_BASE = os.environ.get("E2E_API_BASE", "http://localhost:8080")
MOCKS_BASE = os.environ.get("E2E_MOCKS_BASE", "http://localhost:8090")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:54329/postgres"
)
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "dev-webhook-secret")
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-jwt-secret-change-me-32bytes!!")
ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
CAMPAIGN_ID = UUID("00000000-0000-4000-8000-000000000010")
# Each full run claims ~10 contacts; seed only has 5, so we top up.
MIN_CLAIMABLE_CONTACTS = 16

BASE_TRANSCRIPT = (
    "Менеджер: Здравствуйте! Расскажу про тарифы.\n"
    "Клиент: Интересно, но дорого.\n"
    "Менеджер: Могу предложить скидку.\n"
    "Клиент: Пришлите предложение."
)

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sign(raw: bytes) -> str:
    digest = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def make_token(role: str) -> str:
    import jwt  # local import keeps --help usable without PyJWT installed

    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": f"e2e-{role}",
            "org_id": str(ORG_ID),
            "role": role,
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def webhook_body(
    *,
    call_id: str,
    sequence: int,
    type_: str,
    data: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> bytes:
    return orjson.dumps(
        {
            "event_id": event_id or f"evt_{uuid4().hex}",
            "call_id": call_id,
            "sequence": sequence,
            "type": type_,
            "occurred_at": _now_iso(),
            "data": data or {},
        }
    )


class Check:
    """Accumulates named assertions for the final summary."""

    def __init__(self) -> None:
        self.results: list[tuple[bool, str]] = []

    def record(self, ok: bool, label: str) -> None:
        self.results.append((ok, label))
        mark = "ok" if ok else "FAIL"
        print(f"    [{mark}] {label}", flush=True)

    def expect(self, cond: bool, label: str) -> bool:
        self.record(cond, label)
        return cond

    @property
    def failed(self) -> int:
        return sum(1 for ok, _ in self.results if not ok)


@dataclass
class Ctx:
    client: httpx.AsyncClient
    worker_headers: dict[str, str]
    user_headers: dict[str, str]
    check: Check
    mocks: httpx.AsyncClient


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def ensure_claimable_contacts(n: int = MIN_CLAIMABLE_CONTACTS) -> None:
    """Guarantee enough free contacts for a full e2e run (idempotent top-up)."""
    import asyncpg

    conn = await asyncpg.connect(DATABASE_URL)
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
        free = await conn.fetchval(
            """
            SELECT count(*) FROM contacts
            WHERE campaign_id = $1
              AND do_not_call = false
              AND locked_attempt_id IS NULL
              AND attempts_count < 3
            """,
            CAMPAIGN_ID,
        )
        need = max(0, n - int(free or 0))
        for i in range(need):
            await conn.execute(
                """
                INSERT INTO contacts (
                    id, org_id, campaign_id, phone_e164, timezone,
                    attempts_count, do_not_call
                )
                VALUES ($1, $2, $3, $4, 'Europe/Moscow', 0, false)
                """,
                uuid4(),
                ORG_ID,
                CAMPAIGN_ID,
                f"+7900{uuid4().hex[:7]}",
            )
        if need:
            print(f"seeded {need} extra claimable contacts", flush=True)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# API primitives
# ---------------------------------------------------------------------------


async def claim(ctx: Ctx) -> tuple[str, str]:
    r = await ctx.client.post(
        f"{API_BASE}/rpc/claim_next_contact",
        json={"campaign_id": str(CAMPAIGN_ID)},
        headers=ctx.worker_headers,
    )
    r.raise_for_status()
    contact = r.json()["contact"]
    assert contact is not None, "no claimable contacts left (run `make seed`?)"
    return contact["id"], contact["attempt_id"]


async def provider_link(ctx: Ctx, attempt_id: str, call_id: str) -> httpx.Response:
    return await ctx.client.post(
        f"{API_BASE}/api/call_attempts/{attempt_id}/provider-link",
        json={"provider_call_id": call_id},
        headers=ctx.worker_headers,
    )


async def send_webhook(
    ctx: Ctx,
    raw: bytes,
    *,
    signature: str | None = None,
) -> httpx.Response:
    return await ctx.client.post(
        f"{API_BASE}/webhooks/calls",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Signature": signature if signature is not None else sign(raw),
        },
    )


async def get_attempt(ctx: Ctx, attempt_id: str) -> dict[str, Any]:
    r = await ctx.client.get(
        f"{API_BASE}/api/call_attempts/{attempt_id}", headers=ctx.user_headers
    )
    r.raise_for_status()
    return r.json()


async def get_analysis(ctx: Ctx, analysis_id: str) -> dict[str, Any]:
    r = await ctx.client.get(
        f"{API_BASE}/api/analyses/{analysis_id}", headers=ctx.user_headers
    )
    r.raise_for_status()
    return r.json()


async def wait_for(
    label: str,
    fetch,
    pred,
    *,
    timeout: float,
    interval: float = 0.5,
) -> dict[str, Any]:
    """Poll ``fetch`` until ``pred(body)`` holds; return the last body."""
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = await fetch()
        if pred(body):
            return body
        await asyncio.sleep(interval)
    raise TimeoutError(f"timeout waiting for {label}; last={body!r}")


async def wait_attempt_status(
    ctx: Ctx, attempt_id: str, *statuses: str, timeout: float = 10.0
) -> dict[str, Any]:
    return await wait_for(
        f"attempt {attempt_id[:8]} status in {statuses}",
        lambda: get_attempt(ctx, attempt_id),
        lambda b: b.get("status") in statuses,
        timeout=timeout,
    )


async def wait_crm_state(
    ctx: Ctx, attempt_id: str, *states: str, timeout: float = 30.0
) -> dict[str, Any]:
    return await wait_for(
        f"crm {attempt_id[:8]} state in {states}",
        lambda: get_attempt(ctx, attempt_id),
        lambda b: (b.get("crm") or {}).get("state") in states,
        timeout=timeout,
    )


async def wait_analysis_status(
    ctx: Ctx, analysis_id: str, *statuses: str, timeout: float = 30.0
) -> dict[str, Any]:
    return await wait_for(
        f"analysis {analysis_id[:8]} status in {statuses}",
        lambda: get_analysis(ctx, analysis_id),
        lambda b: b.get("status") in statuses,
        timeout=timeout,
    )


async def drive_call(
    ctx: Ctx,
    attempt_id: str,
    call_id: str,
    transcript: str,
    *,
    terminal: str = "call.completed",
) -> None:
    """Happy-path webhook sequence: dialing -> answered -> terminal."""
    seq = 1
    for type_, data in (
        ("call.dialing", {}),
        ("call.answered", {}),
        (terminal, {"transcript": transcript, "duration_sec": 42}),
    ):
        raw = webhook_body(call_id=call_id, sequence=seq, type_=type_, data=data)
        r = await send_webhook(ctx, raw)
        r.raise_for_status()
        seq += 1


async def create_analysis(ctx: Ctx, attempt_id: str) -> httpx.Response:
    return await ctx.client.post(
        f"{API_BASE}/api/analyses",
        json={"call_attempt_id": attempt_id},
        headers=ctx.user_headers,
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def scenario_happy(ctx: Ctx) -> None:
    print("\n== happy: claim -> webhooks -> CRM delivered -> analysis done ==", flush=True)
    _, attempt_id = await claim(ctx)
    call_id = f"call_e2e_happy_{uuid4().hex[:12]}"

    r = await provider_link(ctx, attempt_id, call_id)
    ctx.check.expect(r.status_code == 200, "provider-link 200")

    await drive_call(ctx, attempt_id, call_id, BASE_TRANSCRIPT)
    body = await wait_attempt_status(ctx, attempt_id, "completed")
    ctx.check.expect(body["status"] == "completed", "attempt completed")
    ctx.check.expect(bool(body.get("transcript")), "transcript stored")

    crm = await wait_crm_state(ctx, attempt_id, "delivered")
    ctx.check.expect(crm["crm"]["state"] == "delivered", "CRM delivered")

    created = await create_analysis(ctx, attempt_id)
    ctx.check.expect(created.status_code == 200, "analysis created")
    analysis_id = created.json()["id"]
    final = await wait_analysis_status(ctx, analysis_id, "done", timeout=40.0)
    result = final.get("result") or {}
    ctx.check.expect(
        result.get("lead_score") == 62 and bool(result.get("summary")),
        "analysis done with valid result",
    )


async def scenario_webhook_chaos(ctx: Ctx) -> None:
    print(
        "\n== webhook chaos: buffer-before-link, dup event, out-of-order, "
        "terminal guard, bad signature ==",
        flush=True,
    )
    _, attempt_id = await claim(ctx)
    call_id = f"call_e2e_chaos_{uuid4().hex[:12]}"

    # 1. Events BEFORE provider-link -> buffered (200, nothing applied yet).
    raw_dial = webhook_body(call_id=call_id, sequence=1, type_="call.dialing")
    r = await send_webhook(ctx, raw_dial)
    ctx.check.expect(r.status_code == 200, "pre-link dialing buffered (200)")
    body = await get_attempt(ctx, attempt_id)
    ctx.check.expect(body["status"] == "queued", "status still queued before link")

    # 2. Answered with a LOWER sequence arriving early is buffered too.
    raw_answer_early = webhook_body(
        call_id=call_id, sequence=2, type_="call.answered"
    )
    await send_webhook(ctx, raw_answer_early)

    # 3. Link -> sweep applies buffered events in order.
    r = await provider_link(ctx, attempt_id, call_id)
    ctx.check.expect(r.status_code == 200, "provider-link 200")
    body = await wait_attempt_status(ctx, attempt_id, "in_progress")
    ctx.check.expect(
        body["status"] == "in_progress",
        "buffer swept to in_progress after link",
    )

    # 4. Duplicate event (same provider_event_id) -> dedup, still 200.
    r = await send_webhook(ctx, raw_dial)
    ctx.check.expect(r.status_code == 200, "duplicate event_id -> 200 (dedup)")

    # 5. Out-of-order: replay sequence 1 -> ignored by sequence guard.
    raw_old = webhook_body(call_id=call_id, sequence=1, type_="call.dialing")
    await send_webhook(ctx, raw_old)
    body = await get_attempt(ctx, attempt_id)
    ctx.check.expect(
        body["status"] == "in_progress", "stale sequence ignored (no regression)"
    )

    # 6. Invalid signature -> 401, nothing written.
    raw_bad = webhook_body(call_id=call_id, sequence=3, type_="call.completed")
    r = await send_webhook(ctx, raw_bad, signature="sha256=deadbeef")
    ctx.check.expect(r.status_code == 401, "invalid signature -> 401")

    # 7. Terminal completes; a later non-terminal event is rejected by guard.
    raw_done = webhook_body(
        call_id=call_id,
        sequence=3,
        type_="call.completed",
        data={"transcript": BASE_TRANSCRIPT},
    )
    await send_webhook(ctx, raw_done)
    body = await wait_attempt_status(ctx, attempt_id, "completed")
    ctx.check.expect(body["status"] == "completed", "completed applied")

    raw_after = webhook_body(call_id=call_id, sequence=4, type_="call.answered")
    await send_webhook(ctx, raw_after)
    body = await get_attempt(ctx, attempt_id)
    ctx.check.expect(
        body["status"] == "completed", "terminal guard: post-completion ignored"
    )


async def _analysis_from_transcript(
    ctx: Ctx, transcript: str, *, timeout: float = 45.0
) -> tuple[str, dict[str, Any]]:
    _, attempt_id = await claim(ctx)
    call_id = f"call_e2e_llm_{uuid4().hex[:12]}"
    await provider_link(ctx, attempt_id, call_id)
    await drive_call(ctx, attempt_id, call_id, transcript)
    await wait_attempt_status(ctx, attempt_id, "completed")
    created = await create_analysis(ctx, attempt_id)
    created.raise_for_status()
    analysis_id = created.json()["id"]
    final = await wait_analysis_status(
        ctx, analysis_id, "done", "error", "cancelled", timeout=timeout
    )
    return analysis_id, final


async def scenario_llm_429(ctx: Ctx) -> None:
    print("\n== llm 429: provider rate-limit -> consumer retry -> done ==", flush=True)
    _, final = await _analysis_from_transcript(
        ctx, BASE_TRANSCRIPT + "\nCHAOS_429", timeout=50.0
    )
    ctx.check.expect(final["status"] == "done", "429 retried then done")


async def scenario_llm_break(ctx: Ctx) -> None:
    print(
        "\n== llm break: provider drops mid-stream -> partial kept, retry -> done ==",
        flush=True,
    )
    _, final = await _analysis_from_transcript(
        ctx, BASE_TRANSCRIPT + "\nCHAOS_BREAK", timeout=50.0
    )
    ctx.check.expect(final["status"] == "done", "stream break recovered to done")
    ctx.check.expect(
        bool((final.get("partial") or {}).get("summary")),
        "partial saved across break",
    )


async def scenario_llm_invalid(ctx: Ctx) -> None:
    print(
        "\n== llm invalid: schema-invalid terminal -> error with partial preserved ==",
        flush=True,
    )
    _, final = await _analysis_from_transcript(
        ctx, BASE_TRANSCRIPT + "\nCHAOS_INVALID", timeout=50.0
    )
    ctx.check.expect(final["status"] == "error", "invalid result -> error")
    ctx.check.expect(
        "invalid provider result" in (final.get("error") or ""),
        "error mentions schema validation",
    )
    ctx.check.expect(
        bool((final.get("partial") or {}).get("summary")),
        "partial kept after invalid terminal",
    )


async def scenario_crm_retry(ctx: Ctx) -> None:
    # Poller backoff is 2^attempts (cap 300s): fail_n=5 → 2+4+8+16+32 ≈ 62s.
    # Dominates wall-clock so a full e2e run lands in the 1–2 minute band.
    fail_n = 5
    print(
        f"\n== crm retry: outcome.__fail_n={fail_n} -> retrying -> delivered ==",
        flush=True,
    )
    _, attempt_id = await claim(ctx)
    call_id = f"call_e2e_crm_{uuid4().hex[:12]}"
    await provider_link(ctx, attempt_id, call_id)

    # __fail_n lands in call_attempts.outcome and is re-POSTed by the CRM poller.
    seq = 1
    for type_, data in (
        ("call.dialing", {}),
        ("call.answered", {}),
        (
            "call.completed",
            {
                "transcript": BASE_TRANSCRIPT,
                "duration_sec": 42,
                "__fail_n": fail_n,
            },
        ),
    ):
        raw = webhook_body(call_id=call_id, sequence=seq, type_=type_, data=data)
        (await send_webhook(ctx, raw)).raise_for_status()
        seq += 1

    await wait_attempt_status(ctx, attempt_id, "completed")

    retrying = await wait_crm_state(
        ctx, attempt_id, "retrying", "delivered", timeout=30.0
    )
    ctx.check.expect(
        retrying["crm"]["state"] in ("retrying", "delivered"),
        "CRM entered retry path",
    )
    delivered = await wait_crm_state(ctx, attempt_id, "delivered", timeout=120.0)
    crm = delivered["crm"]
    ctx.check.expect(crm["state"] == "delivered", "CRM eventually delivered")
    ctx.check.expect(
        crm["attempts"] >= fail_n,
        f"CRM attempts>={fail_n} after fail_n (got {crm['attempts']})",
    )

    dbg = await ctx.mocks.get(f"{MOCKS_BASE}/crm/_debug/calls")
    dbg.raise_for_status()
    snap = dbg.json()
    ctx.check.expect(
        snap.get("failed_calls", 0) >= fail_n,
        f"CRM mock failed_calls>={fail_n} (got {snap.get('failed_calls')})",
    )


async def scenario_validation(ctx: Ctx) -> None:
    print("\n== validation: 409 attempt_not_completed / no_transcript ==", flush=True)

    # Queued (non-terminal) attempt -> attempt_not_completed.
    _, attempt_id = await claim(ctx)
    created = await create_analysis(ctx, attempt_id)
    ctx.check.expect(
        created.status_code == 409
        and created.json().get("code") == "attempt_not_completed",
        "409 attempt_not_completed",
    )

    # Completed without transcript -> no_transcript (failed would be attempt_not_completed).
    _, attempt_id2 = await claim(ctx)
    call_id = f"call_e2e_val_{uuid4().hex[:12]}"
    await provider_link(ctx, attempt_id2, call_id)
    seq = 1
    for type_, data in (
        ("call.dialing", {}),
        ("call.answered", {}),
        ("call.completed", {"duration_sec": 5}),  # no transcript key
    ):
        raw = webhook_body(call_id=call_id, sequence=seq, type_=type_, data=data)
        (await send_webhook(ctx, raw)).raise_for_status()
        seq += 1
    body = await wait_attempt_status(ctx, attempt_id2, "completed")
    ctx.check.expect(not body.get("transcript"), "completed without transcript")
    created2 = await create_analysis(ctx, attempt_id2)
    ctx.check.expect(
        created2.status_code == 409 and created2.json().get("code") == "no_transcript",
        "409 no_transcript",
    )


async def scenario_cancel(ctx: Ctx) -> None:
    print("\n== cancel: abort streaming analysis -> cancelled ==", flush=True)
    _, attempt_id = await claim(ctx)
    call_id = f"call_e2e_cancel_{uuid4().hex[:12]}"
    await provider_link(ctx, attempt_id, call_id)
    await drive_call(ctx, attempt_id, call_id, BASE_TRANSCRIPT)
    await wait_attempt_status(ctx, attempt_id, "completed")

    created = await create_analysis(ctx, attempt_id)
    created.raise_for_status()
    analysis_id = created.json()["id"]
    # Wait until the consumer picks it up, then cancel.
    await wait_analysis_status(ctx, analysis_id, "streaming", timeout=15.0)
    r = await ctx.client.post(
        f"{API_BASE}/api/analyses/{analysis_id}/cancel", headers=ctx.user_headers
    )
    ctx.check.expect(r.status_code == 200, "cancel 200")
    final = await wait_analysis_status(ctx, analysis_id, "cancelled", timeout=15.0)
    ctx.check.expect(final["status"] == "cancelled", "analysis cancelled")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

SCENARIOS = [
    ("happy", scenario_happy),
    ("webhook_chaos", scenario_webhook_chaos),
    ("llm_429", scenario_llm_429),
    ("llm_break", scenario_llm_break),
    ("llm_invalid", scenario_llm_invalid),
    ("crm_retry", scenario_crm_retry),
    ("validation", scenario_validation),
    ("cancel", scenario_cancel),
]


async def run(selected: list[str] | None) -> int:
    check = Check()
    worker_token = make_token("worker")
    user_token = make_token("authenticated")

    timeout = httpx.Timeout(15.0)
    async with httpx.AsyncClient(timeout=timeout) as client, httpx.AsyncClient(
        timeout=timeout
    ) as mocks:
        ctx = Ctx(
            client=client,
            worker_headers={"Authorization": f"Bearer {worker_token}"},
            user_headers={"Authorization": f"Bearer {user_token}"},
            check=check,
            mocks=mocks,
        )

        # Preflight: API + mocks reachable; top up claimable contacts.
        try:
            hz = await client.get(f"{API_BASE}/healthz")
            hz.raise_for_status()
            mz = await mocks.get(f"{MOCKS_BASE}/healthz")
            mz.raise_for_status()
            await mocks.post(f"{MOCKS_BASE}/crm/_chaos", json={"fail_n": 0})
            await ensure_claimable_contacts()
        except Exception as exc:  # noqa: BLE001
            print(
                f"stand not ready ({exc}). Run:\n"
                "  docker compose --profile dev up -d && make seed",
                file=sys.stderr,
            )
            return 2

        started = time.monotonic()
        for name, fn in SCENARIOS:
            if selected and name not in selected:
                continue
            t0 = time.monotonic()
            try:
                await fn(ctx)
            except Exception as exc:  # noqa: BLE001
                check.record(False, f"{name} crashed: {exc!r}")
            print(f"-- {name} took {time.monotonic() - t0:.1f}s", flush=True)

    total = time.monotonic() - started
    passed = len(check.results) - check.failed
    print(
        f"\n{'=' * 60}\n"
        f"checks: {passed}/{len(check.results)} passed, {check.failed} failed, "
        f"elapsed {total:.1f}s",
        flush=True,
    )
    if check.failed:
        for ok, label in check.results:
            if not ok:
                print(f"  FAILED: {label}", flush=True)
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        nargs="*",
        choices=[n for n, _ in SCENARIOS],
        default=None,
        help="run only the listed scenarios",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.only)))


if __name__ == "__main__":
    main()
