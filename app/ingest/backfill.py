from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.config import GroupConfig
from app.ingest.persist import persist_message
from app.whapi.client import WhapiClient

logger = logging.getLogger(__name__)

PAGE_SIZE = 200


def backfill_group(client: WhapiClient, session: Session, group: GroupConfig, since: dt.datetime) -> int:
    """Pull a group's message history back to `since` via paginated /messages/list. Returns inserted count."""
    time_from = int(since.timestamp())
    offset = 0
    inserted = 0

    while True:
        page = client.get_messages(group.id, count=PAGE_SIZE, offset=offset, time_from=time_from, sort="asc")
        if not page.messages:
            break

        for message in page.messages:
            if persist_message(session, message, group):
                inserted += 1

        if len(page.messages) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return inserted


def backfill_groups(client: WhapiClient, session: Session, groups: list[GroupConfig], days: int) -> dict[str, int]:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    results: dict[str, int] = {}
    for group in groups:
        if not group.enabled:
            continue
        logger.info("Backfilling %s (%s) since %s", group.name, group.id, since.isoformat())
        results[group.id] = backfill_group(client, session, group, since)
        session.commit()
    return results
