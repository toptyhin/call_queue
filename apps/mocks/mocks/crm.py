"""CRM mock: log POST bodies and return 200 (or 500 for retry tests).

Chaos controls for e2e:
- ``X-Fail: 1`` header or ``{"__fail": true}`` body → always 500.
- ``__fail_n`` in body (usually inside outbox ``outcome``) → first N POSTs
  for that ``attempt_id`` return 500, then 200.
- ``POST /crm/_chaos {"fail_n": N}`` → arm N global 500 responses (any body).
- ``GET /crm/_debug/calls`` → counters for assertions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("mocks.crm")

router = APIRouter()


class _CrmChaos:
    """In-memory failure budgets (single-process mock)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.global_fail_n = 0
        # remaining failures per attempt_id (from body ``__fail_n``)
        self.per_attempt: dict[str, int] = {}
        self.total_calls = 0
        self.failed_calls = 0

    async def arm_global(self, n: int) -> None:
        async with self._lock:
            self.global_fail_n = max(0, n)

    async def decide_fail(
        self, *, attempt_id: str | None, body_fail_n: int
    ) -> bool:
        """Return True when this ingest should respond 500."""
        async with self._lock:
            self.total_calls += 1

            if attempt_id and body_fail_n > 0 and attempt_id not in self.per_attempt:
                self.per_attempt[attempt_id] = body_fail_n

            if attempt_id and self.per_attempt.get(attempt_id, 0) > 0:
                self.per_attempt[attempt_id] -= 1
                self.failed_calls += 1
                return True

            if self.global_fail_n > 0:
                self.global_fail_n -= 1
                self.failed_calls += 1
                return True

            return False

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "total_calls": self.total_calls,
                "failed_calls": self.failed_calls,
                "fail_n_remaining": self.global_fail_n,
                "per_attempt_remaining": dict(self.per_attempt),
            }


state = _CrmChaos()


def _permanent_fail(body: Any, request: Request) -> bool:
    if request.headers.get("X-Fail") == "1":
        return True
    if isinstance(body, dict) and body.get("__fail") is True:
        return True
    return False


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _body_fail_n(body: Any) -> int:
    if not isinstance(body, dict):
        return 0
    # Prefer top-level; fall back to nested outcome (poller may send jsonb as str).
    raw = body.get("__fail_n")
    if raw is None:
        outcome = _as_dict(body.get("outcome"))
        if outcome is not None:
            raw = outcome.get("__fail_n")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return max(0, raw)
    return 0


def _attempt_id(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    raw = body.get("attempt_id")
    return str(raw) if raw is not None else None


@router.post("/_chaos")
async def set_chaos(request: Request) -> JSONResponse:
    body = await request.json()
    n = int(body.get("fail_n", 0)) if isinstance(body, dict) else 0
    await state.arm_global(n)
    return JSONResponse({"ok": True, "fail_n": max(0, n)})


@router.get("/_debug/calls")
async def debug_calls() -> dict[str, Any]:
    return await state.snapshot()


@router.api_route("", methods=["POST", "PUT", "PATCH"])
@router.api_route("/", methods=["POST", "PUT", "PATCH"])
@router.api_route("/{path:path}", methods=["POST", "PUT", "PATCH"])
async def crm_ingest(request: Request, path: str = "") -> Response:
    # Avoid swallowing control endpoints if route order ever changes.
    if path in ("_chaos", "_debug/calls"):
        return JSONResponse({"ok": False, "error": "use dedicated route"}, status_code=404)

    raw = await request.body()
    parsed: Any
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = raw.decode("utf-8", errors="replace")

    print(
        json.dumps(
            {
                "event": "crm_request",
                "method": request.method,
                "path": f"/{path}" if path else "/",
                "body": parsed,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    logger.info("crm_request path=%s body=%s", path or "/", parsed)

    if _permanent_fail(parsed, request):
        return JSONResponse({"ok": False, "error": "forced_fail"}, status_code=500)

    if await state.decide_fail(
        attempt_id=_attempt_id(parsed),
        body_fail_n=_body_fail_n(parsed),
    ):
        return JSONResponse({"ok": False, "error": "chaos"}, status_code=500)

    return JSONResponse({"ok": True})
