from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import GroupConfig, Settings
from app.db.models import DailySummary, Message
from app.summarize.aliases import Pseudonymizer, summary_has_raw_numbers
from app.summarize.pass1 import AnthropicLike, summarize_day


def _day_bounds_utc(date: dt.date, tz: ZoneInfo) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(date, dt.time.min, tzinfo=tz).astimezone(dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    return start, end


def get_or_build_daily_summary(
    session: Session,
    client: AnthropicLike,
    settings: Settings,
    group: GroupConfig,
    date: dt.date,
    pseudo: Pseudonymizer | None = None,
) -> tuple[DailySummary, bool]:
    """Return the DailySummary for (group, date), generating and caching it if missing.

    Only *completed* days are cached: a summary built while its day was still in progress is
    incomplete, so today's is always rebuilt fresh and never stored, and any cached row that was
    created before its day ended is discarded. Returns (row, was_cached).
    """
    tz = ZoneInfo(settings.timezone)
    start, end = _day_bounds_utc(date, tz)
    if pseudo is None:
        pseudo = Pseudonymizer(session, settings.member_alias_prefix)

    existing = session.execute(
        select(DailySummary).where(DailySummary.group_id == group.id, DailySummary.summary_date == date)
    ).scalar_one_or_none()
    if existing is not None:
        created_at = existing.created_at
        if created_at.tzinfo is None:  # SQLite hands back naive datetimes; we store UTC
            created_at = created_at.replace(tzinfo=dt.timezone.utc)
        # Also rebuild a summary that names people by phone number: it predates member labels.
        stale_numbers = pseudo.enabled and summary_has_raw_numbers(existing.summary_json)
        if created_at >= end and not stale_numbers:
            return existing, True
        session.delete(existing)
        session.flush()

    day_is_over = dt.datetime.now(dt.timezone.utc) >= end
    messages = list(
        session.execute(
            select(Message)
            .where(Message.group_id == group.id, Message.timestamp_utc >= start, Message.timestamp_utc < end)
            .order_by(Message.timestamp_utc.asc())
        ).scalars()
    )

    summary_json, input_tokens, output_tokens = summarize_day(
        client, settings, group.name, date, messages, pseudo
    )

    row = DailySummary(
        group_id=group.id,
        summary_date=date,
        summary_json=summary_json,
        model=settings.anthropic_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    if day_is_over:
        session.add(row)
        session.flush()
    return row, False
