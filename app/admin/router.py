from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.admin.auth import require_admin, require_json_for_writes
from app.admin.jobs import AlreadyRunning, jobs
from app.config import get_settings
from app.db.models import Group, MemberAlias, Message
from app.db.session import session_scope
from app.whapi.client import WhapiClient

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
WHAPI_LIST_TTL_SECONDS = 60

# require_admin must come first so unauthenticated callers get 401, not 415.
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin), Depends(require_json_for_writes)])

_whapi_cache: dict = {"at": 0.0, "groups": []}
_whapi_cache_lock = threading.Lock()


class AddGroup(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class UpdateGroup(BaseModel):
    enabled: bool | None = None
    notes: str | None = None


class BackfillRequest(BaseModel):
    days: int = Field(ge=1, le=30)


def _group_dict(row: Group, stats: dict) -> dict:
    count, last = stats.get(row.id, (0, None))
    job = jobs.latest_for_group(row.id)
    return {
        "id": row.id,
        "name": row.name,
        "enabled": row.enabled,
        "notes": row.notes or "",
        "message_count": count,
        "last_message_at": last.isoformat() if last else None,
        "backfill": job.as_dict() if job else None,
    }


@router.get("", include_in_schema=False)
def page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@router.get("/api/groups")
def list_groups() -> list[dict]:
    with session_scope() as session:
        rows = session.execute(select(Group).order_by(Group.name)).scalars().all()
        stats = {
            group_id: (count, last)
            for group_id, count, last in session.execute(
                select(Message.group_id, func.count(), func.max(Message.timestamp_utc)).group_by(Message.group_id)
            )
        }
        return [_group_dict(r, stats) for r in rows]


@router.get("/api/whapi-groups")
def list_whapi_groups(refresh: bool = False) -> list[dict]:
    """Every group on the linked WhatsApp number, flagged if already on the watchlist."""
    with _whapi_cache_lock:
        fresh = time.monotonic() - _whapi_cache["at"] < WHAPI_LIST_TTL_SECONDS
        if refresh or not fresh:
            try:
                _whapi_cache["groups"] = _fetch_whapi_groups()
            except Exception as exc:
                logger.exception("Could not list groups from Whapi")
                raise HTTPException(status_code=502, detail=f"Could not reach Whapi: {type(exc).__name__}") from exc
            _whapi_cache["at"] = time.monotonic()
        groups = list(_whapi_cache["groups"])

    with session_scope() as session:
        on_watchlist = set(session.execute(select(Group.id)).scalars())
    return [{**g, "on_watchlist": g["id"] in on_watchlist} for g in groups]


def _fetch_whapi_groups() -> list[dict]:
    found: list[dict] = []
    with WhapiClient(get_settings()) as client:
        offset, count = 0, 100
        while True:
            page = client.get_groups(count=count, offset=offset)
            found.extend(
                {"id": g.id, "name": g.name, "participants_count": g.participants_count} for g in page.groups
            )
            if len(page.groups) < count:
                break
            offset += count
    return sorted(found, key=lambda g: g["name"].lower())


@router.get("/api/aliases")
def list_aliases() -> list[dict]:
    """Which number each anonymized label (SMM7, ...) stands for. Admin-only by design."""
    with session_scope() as session:
        rows = session.execute(select(MemberAlias)).scalars().all()
        rows.sort(key=lambda r: (len(r.label), r.label))  # SMM2 before SMM10
        return [{"label": r.label, "number": r.local_id} for r in rows]


@router.post("/api/groups", status_code=201)
def add_group(body: AddGroup) -> dict:
    if not body.id.endswith("@g.us"):
        raise HTTPException(status_code=422, detail="Group ids end in @g.us")
    with session_scope() as session:
        if session.get(Group, body.id) is not None:
            raise HTTPException(status_code=409, detail="Group is already on the watchlist")
        row = Group(id=body.id, name=body.name.strip(), enabled=True, notes="")
        session.add(row)
        session.flush()
        return _group_dict(row, {})


@router.patch("/api/groups/{group_id}")
def update_group(group_id: str, body: UpdateGroup) -> dict:
    with session_scope() as session:
        row = session.get(Group, group_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown group")
        if body.enabled is not None:
            row.enabled = body.enabled
        if body.notes is not None:
            row.notes = body.notes
        session.flush()
        return _group_dict(row, {})


@router.post("/api/groups/{group_id}/backfill", status_code=202)
def start_backfill(group_id: str, body: BackfillRequest) -> dict:
    with session_scope() as session:
        if session.get(Group, group_id) is None:
            raise HTTPException(status_code=404, detail="Unknown group")
    try:
        job = jobs.start_backfill(group_id, body.days)
    except AlreadyRunning:
        raise HTTPException(status_code=409, detail="A backfill for this group is already running")
    return {"job_id": job.id}


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job.as_dict()
