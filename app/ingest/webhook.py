from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.config import get_settings
from app.db.session import session_scope
from app.ingest.persist import persist_message
from app.whapi.schemas import WebhookPayload

logger = logging.getLogger(__name__)

router = APIRouter()

ACCEPTED_EVENT_TYPES = {"messages"}


@router.post("/webhook/whapi")
async def receive_webhook(request: Request) -> dict:
    body = await request.json()
    payload = WebhookPayload.model_validate(body)

    if payload.event and payload.event.type not in ACCEPTED_EVENT_TYPES:
        return {"ingested": 0, "skipped_reason": "ignored_event_type"}

    settings = get_settings()
    watchlist = {g.id: g for g in settings.load_groups() if g.enabled}

    raw_messages = body.get("messages", [])
    inserted = 0
    skipped = 0

    with session_scope() as session:
        for message, raw in zip(payload.messages, raw_messages):
            group = watchlist.get(message.chat_id)
            if group is None:
                skipped += 1
                continue
            if persist_message(session, message, group, raw=raw):
                inserted += 1
            else:
                skipped += 1

    return {"ingested": inserted, "skipped": skipped}
