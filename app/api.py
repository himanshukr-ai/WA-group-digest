from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.session import init_db
from app.ingest.webhook import router as webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="WhatsApp Digest Bot", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
