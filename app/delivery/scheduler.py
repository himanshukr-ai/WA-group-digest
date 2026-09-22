from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings, get_settings
from app.db.session import session_scope
from app.summarize.pass1 import AnthropicLike
from app.summarize.pass2 import build_digest
from app.whapi.client import WhapiClient

logger = logging.getLogger(__name__)


def run_daily_digest(settings: Settings | None = None, anthropic_client: AnthropicLike | None = None) -> None:
    """Build the last-24h digest and send it to the user's own WhatsApp chat."""
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

    groups = settings.load_groups()

    with session_scope() as session:
        digest_text, usage = build_digest(session, anthropic_client, settings, groups, window_days=1)

    with WhapiClient(settings) as client:
        client.send_text(settings.self_chat_id, digest_text)

    logger.info(
        "Sent scheduled daily digest: %d input tokens, %d output tokens, ~$%.4f",
        usage["input_tokens"], usage["output_tokens"], usage["cost"],
    )


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
    return scheduler
