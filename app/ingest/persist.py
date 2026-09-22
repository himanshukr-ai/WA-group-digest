from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import GroupConfig
from app.db.models import Message
from app.whapi.schemas import WhapiMessage

# WhatsApp anonymizes hidden-number participants as "<id>@lid". When that's the
# sender id, prefer the display name since the number itself isn't usable.
LID_SUFFIX = "@lid"


def sender_display_name(message: WhapiMessage) -> str:
    if message.from_name:
        return message.from_name
    if message.from_ and not message.from_.endswith(LID_SUFFIX):
        return message.from_
    return "Unknown"


def persist_message(session: Session, message: WhapiMessage, group: GroupConfig, raw: dict | None = None) -> bool:
    """Store a message if not already present. Returns True if a new row was inserted."""
    existing = session.get(Message, message.id)
    if existing is not None:
        return False

    text_body = message.text.body if message.text else None
    quoted_id = message.context.quoted_id if message.context else None
    quoted_content = message.context.quoted_content if message.context else None
    quoted_text = None
    if quoted_content and isinstance(quoted_content, dict):
        quoted_text = quoted_content.get("body") or quoted_content.get("text", {}).get("body")

    row = Message(
        message_id=message.id,
        group_id=group.id,
        group_name=group.name,
        sender_id=message.from_ or "",
        sender_name=sender_display_name(message),
        timestamp_utc=dt.datetime.fromtimestamp(message.timestamp, tz=dt.timezone.utc),
        type=message.type,
        text=text_body,
        quoted_message_id=quoted_id,
        quoted_text=quoted_text,
        raw_json=raw if raw is not None else message.model_dump(mode="json"),
    )
    session.add(row)
    return True


def already_ingested(session: Session, message_id: str) -> bool:
    return session.execute(select(Message.message_id).where(Message.message_id == message_id)).first() is not None
