from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import GroupConfig, Settings
from app.db.models import DailySummary, Message
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
) -> tuple[DailySummary, bool]:
    """Return the cached DailySummary for (group, date), generating and caching it if missing.

    Returns (row, was_cached).
    """
    existing = session.execute(
        select(DailySummary).where(DailySummary.group_id == group.id, DailySummary.summary_date == date)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, True

    tz = ZoneInfo(settings.timezone)
    start, end = _day_bounds_utc(date, tz)
    messages = list(
        session.execute(
            select(Message)
            .where(Message.group_id == group.id, Message.timestamp_utc >= start, Message.timestamp_utc < end)
            .order_by(Message.timestamp_utc.asc())
        ).scalars()
    )

    summary_json, input_tokens, output_tokens = summarize_day(client, settings, group.name, date, messages)

    row = DailySummary(
        group_id=group.id,
        summary_date=date,
        summary_json=summary_json,
        model=settings.anthropic_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    session.add(row)
    session.flush()
    return row, False
