"""CRM mock: log POST bodies and return 200 (or 500 for retry tests)."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("mocks.crm")

router = APIRouter()


def _should_fail(body: Any, request: Request) -> bool:
    if request.headers.get("X-Fail") == "1":
        return True
    if isinstance(body, dict) and body.get("__fail") is True:
        return True
    return False


@router.api_route("", methods=["POST", "PUT", "PATCH"])
@router.api_route("/", methods=["POST", "PUT", "PATCH"])
@router.api_route("/{path:path}", methods=["POST", "PUT", "PATCH"])
async def crm_ingest(request: Request, path: str = "") -> Response:
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

    if _should_fail(parsed, request):
        return JSONResponse({"ok": False, "error": "forced_fail"}, status_code=500)

    return JSONResponse({"ok": True})
