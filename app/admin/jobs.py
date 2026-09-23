from __future__ import annotations

import datetime as dt
import logging
import threading
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass

from app.config import GroupConfig, get_settings
from app.db.models import Group
from app.db.session import session_scope
from app.ingest.backfill import backfill_group
from app.whapi.client import WhapiClient

logger = logging.getLogger(__name__)

MAX_JOBS_KEPT = 50


@dataclass
class Job:
    id: str
    group_id: str
    days: int
    status: str = "running"  # running | done | error
    inserted: int | None = None
    error: str | None = None
    started_at: str = ""
    finished_at: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class AlreadyRunning(Exception):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class JobRegistry:
    """In-memory backfill jobs. The app is a single process, and a backfill is idempotent, so a
    restart losing job status is harmless: just run it again."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, Job] = OrderedDict()

    def reset(self) -> None:
        with self._lock:
            self._jobs.clear()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def latest_for_group(self, group_id: str) -> Job | None:
        with self._lock:
            for job in reversed(self._jobs.values()):
                if job.group_id == group_id:
                    return job
        return None

    def start_backfill(self, group_id: str, days: int) -> Job:
        with self._lock:
            for existing in self._jobs.values():
                if existing.group_id == group_id and existing.status == "running":
                    raise AlreadyRunning(group_id)
            job = Job(id=uuid.uuid4().hex, group_id=group_id, days=days, started_at=_now())
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS_KEPT:
                self._jobs.popitem(last=False)

        threading.Thread(target=self._run_backfill, args=(job.id,), name=f"backfill-{group_id}", daemon=True).start()
        return job

    def _finish(self, job_id: str, *, inserted: int | None = None, error: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "error" if error else "done"
            job.inserted = inserted
            job.error = error
            job.finished_at = _now()

    def _run_backfill(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        try:
            settings = get_settings()
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=job.days)
            with session_scope() as session:
                row = session.get(Group, job.group_id)
                if row is None:
                    raise LookupError(f"group {job.group_id} is not on the watchlist")
                group = GroupConfig(id=row.id, name=row.name, enabled=row.enabled, notes=row.notes or "")
                with WhapiClient(settings) as client:
                    inserted = backfill_group(client, session, group, since)
            self._finish(job_id, inserted=inserted)
        except Exception as exc:  # surfaced to the page; details in the server log
            logger.exception("Backfill job %s failed", job_id)
            self._finish(job_id, error=f"{type(exc).__name__}: {exc}")


jobs = JobRegistry()
