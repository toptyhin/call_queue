"""Combined FastAPI app: LLM provider mock + CRM mock."""

from __future__ import annotations

from fastapi import FastAPI

from mocks.crm import router as crm_router
from mocks.provider import router as provider_router

app = FastAPI(title="call-mocks", version="0.1.0")

app.include_router(provider_router)
app.include_router(crm_router, prefix="/crm")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
