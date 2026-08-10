from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status_code: int, code: str, detail: Any) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(code)


def error_body(code: str, detail: Any) -> dict[str, Any]:
    return {"code": code, "detail": detail}


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.detail),
    )


async def http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    code = "unauthorized"
    if isinstance(detail, dict) and "code" in detail:
        code = str(detail["code"])
        detail = detail.get("detail", detail)
    elif exc.status_code == 403:
        code = "forbidden"
    elif exc.status_code == 404:
        code = "not_found"
    elif exc.status_code == 409:
        code = "validation_error"
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(code, detail if isinstance(detail, (str, list)) else str(detail)),
    )


async def validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_body("validation_error", exc.errors()),
    )
