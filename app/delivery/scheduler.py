from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings, get_settings
from app.db.retention import purge_old_messages
from app.db.session import session_scope
from app.db.sync_groups import load_groups_from_db
from app.summarize.pass1 import AnthropicLike
from app.summarize.pass2 import build_digest
from app.whapi.client import WhapiClient

logger = logging.getLogger(__name__)


def run_daily_digest(settings: Settings | None = None, anthropic_client: AnthropicLike | None = None) -> None:
    """Build the digest for yesterday (the last complete day) and send it to the user's own chat.

    Yesterday rather than "today so far": at 08:00 today has only a few hours of messages, and
    each message then lands in exactly one scheduled digest with no overlap or gaps. For anything
    more recent, use /digest.
    """
    settings = settings or get_settings()

    if not settings.self_chat_id:
        logger.warning("SELF_NUMBER is not configured; skipping scheduled digest")
        return
    if not settings.anthropic_api_key and anthropic_client is None:
        logger.warning("ANTHROPIC_API_KEY is not configured; skipping scheduled digest")
        return

    if anthropic_client is None:
        import anthropic

        anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    yesterday = dt.datetime.now(ZoneInfo(settings.timezone)).date() - dt.timedelta(days=1)

    with session_scope() as session:
        groups = load_groups_from_db(session)
        digest_text, usage = build_digest(
            session, anthropic_client, settings, groups, window_days=1, end_date=yesterday
        )

    with WhapiClient(settings) as client:
        client.send_text(settings.self_chat_id, digest_text)

    logger.info(
        "Sent scheduled daily digest: %d input tokens, %d output tokens, ~$%.4f",
        usage["input_tokens"], usage["output_tokens"], usage["cost"],
    )


def run_retention(settings: Settings | None = None) -> int:
    """Delete raw messages past Settings.retention_days. Daily summaries are kept forever."""
    settings = settings or get_settings()
    with session_scope() as session:
        return purge_old_messages(session, settings)


def create_scheduler(settings: Settings | None = None) -> BackgroundScheduler:
    settings = settings or get_settings()
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        run_daily_digest,
        trigger=CronTrigger(hour=settings.daily_digest_hour, minute=0, timezone=settings.timezone),
        args=[settings],
        id="daily_digest",
        replace_existing=True,
    )
    # Runs a few hours before the digest so a shrinking retention window never races the digest
    # that still needs today's (and the window's) messages.
    scheduler.add_job(
        run_retention,
        trigger=CronTrigger(hour=3, minute=0, timezone=settings.timezone),
        args=[settings],
        id="retention",
        replace_existing=True,
    )
    return scheduler
