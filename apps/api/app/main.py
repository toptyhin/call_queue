from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.db import db
from app.errors import AppError, app_error_handler, http_error_handler, validation_error_handler
from app.logging import get_logger, setup_logging
from app.middleware import RequestIdMiddleware
from app.migrations import apply_migrations
from app.routers import dev

log = get_logger(__name__)


async def wait_for_db(dsn: str, attempts: int = 60) -> None:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            conn = await asyncpg.connect(dsn)
            await conn.close()
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            await asyncio.sleep(1)
    raise RuntimeError(f"database not ready: {last}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    settings = get_settings()
    log.info("startup.begin")
    await wait_for_db(settings.database_url)
    await apply_migrations(settings)
    await db.connect(settings)

    log.info("startup.ready")
    yield

    await db.disconnect()
    log.info("shutdown.complete")


app = FastAPI(
    title="Call Campaign Service API",
    lifespan=lifespan,
)
app.add_middleware(RequestIdMiddleware)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)

app.include_router(dev.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
