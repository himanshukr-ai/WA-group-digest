from __future__ import annotations

from pydantic import BaseModel


class QuotedContext(BaseModel):
    quoted_id: str | None = None
    quoted_author: str | None = None
    quoted_content: dict | None = None
    quoted_type: str | None = None


class TextBody(BaseModel):
    body: str


class WhapiMessage(BaseModel):
    id: str
    from_me: bool = False
    type: str
    chat_id: str
    chat_name: str | None = None
    timestamp: int
    from_: str | None = None
    from_name: str | None = None
    source: str | None = None
    text: TextBody | None = None
    context: QuotedContext | None = None

    model_config = {"populate_by_name": True}

    def __init__(self, **data):
        if "from" in data and "from_" not in data:
            data["from_"] = data.pop("from")
        super().__init__(**data)


class WebhookEvent(BaseModel):
    type: str
    event: str


class WebhookPayload(BaseModel):
    messages: list[WhapiMessage] = []
    event: WebhookEvent | None = None
    channel_id: str | None = None


class GroupParticipant(BaseModel):
    id: str
    rank: str


class WhapiGroup(BaseModel):
    id: str
    name: str
    participants_count: int | None = None
    participants: list[GroupParticipant] = []


class GroupsList(BaseModel):
    groups: list[WhapiGroup] = []
    count: int = 0
    total: int = 0
    offset: int = 0


class MessagesList(BaseModel):
    messages: list[WhapiMessage] = []
    count: int = 0
    total: int = 0
    offset: int = 0
