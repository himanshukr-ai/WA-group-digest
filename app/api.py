from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db.session import init_db, session_scope
from app.db.sync_groups import sync_groups
from app.ingest.webhook import router as webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with session_scope() as session:
        sync_groups(session, get_settings().load_groups())
    yield


app = FastAPI(title="WhatsApp Digest Bot", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
