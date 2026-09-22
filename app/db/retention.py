from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Message

logger = logging.getLogger(__name__)


def purge_old_messages(session: Session, settings: Settings) -> int:
    """Delete raw messages older than Settings.retention_days. Daily summaries are kept forever."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=settings.retention_days)
    result = session.execute(delete(Message).where(Message.timestamp_utc < cutoff))
    deleted = result.rowcount or 0
    if deleted:
        logger.info("Retention: deleted %d messages older than %s", deleted, cutoff.date().isoformat())
    return deleted
