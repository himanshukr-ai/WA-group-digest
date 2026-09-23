from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from app.config import get_settings
from app.db.session import init_db, session_scope
from app.admin.router import router as admin_router
from app.db.sync_groups import seed_groups_if_empty
from app.ingest.webhook import router as webhook_router

LOCAL_CLIENTS = {"127.0.0.1", "::1", "testclient"}


def is_local_client(host: str | None) -> bool:
    return host in LOCAL_CLIENTS


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        seed_groups_if_empty(session, settings.load_groups())

    scheduler = None
    if settings.enable_scheduler:
        from app.delivery.scheduler import create_scheduler

        scheduler = create_scheduler(settings)
        scheduler.start()

    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(title="WhatsApp Digest Bot", lifespan=lifespan)
app.include_router(webhook_router)
app.include_router(admin_router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/admin/run-digest")
async def admin_run_digest(request: Request) -> dict:
    client_host = request.client.host if request.client else None
    if not is_local_client(client_host):
        raise HTTPException(status_code=403, detail="Forbidden")

    from app.delivery.scheduler import run_daily_digest

    run_daily_digest()
    return {"status": "triggered"}
