from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.config import Settings, get_settings
from app.db.session import session_scope
from app.db.sync_groups import load_groups_from_db
from app.delivery.commands import ParsedCommand, handle_command, is_self_command, parse_command
from app.ingest.persist import persist_message
from app.whapi.client import WhapiClient
from app.whapi.schemas import WebhookPayload

logger = logging.getLogger(__name__)

router = APIRouter()

ACCEPTED_EVENT_TYPES = {"messages"}


def _run_command(session, settings: Settings, command: ParsedCommand) -> str | None:
    anthropic_client = None
    if settings.anthropic_api_key:
        import anthropic

        anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return handle_command(session, anthropic_client, settings, command)


def _send_self_chat_reply(settings: Settings, text: str) -> None:
    try:
        with WhapiClient(settings) as client:
            client.send_text(settings.self_chat_id, text)
    except Exception:
        logger.exception("Failed to send self-chat reply")


@router.post("/webhook/whapi")
async def receive_webhook(request: Request) -> dict:
    body = await request.json()
    payload = WebhookPayload.model_validate(body)

    if payload.event and payload.event.type not in ACCEPTED_EVENT_TYPES:
        return {"ingested": 0, "skipped_reason": "ignored_event_type"}

    settings = get_settings()

    raw_messages = body.get("messages", [])
    inserted = 0
    skipped = 0
    commands_handled = 0

    with session_scope() as session:
        # Read per request so groups added/disabled from the admin page take effect immediately.
        watchlist = {g.id: g for g in load_groups_from_db(session) if g.enabled}

        for message, raw in zip(payload.messages, raw_messages):
            if is_self_command(message, settings):
                command = parse_command(message.text.body)
                if command is None:
                    skipped += 1
                    continue
                reply = _run_command(session, settings, command)
                if reply:
                    _send_self_chat_reply(settings, reply)
                    commands_handled += 1
                continue

            group = watchlist.get(message.chat_id)
            if group is None:
                skipped += 1
                continue
            if persist_message(session, message, group, raw=raw):
                inserted += 1
            else:
                skipped += 1

    return {"ingested": inserted, "skipped": skipped, "commands_handled": commands_handled}
