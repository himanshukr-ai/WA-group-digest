from __future__ import annotations

import datetime as dt
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db.models import Message
from app.summarize.prompts import render_prompt

# Rough character budget per pass-1 call. Keeps a single day's messages comfortably
# within a small-model context window without needing an exact tokenizer.
CHUNK_CHAR_BUDGET = 12_000

EMPTY_SUMMARY: dict[str, list] = {
    "topics": [],
    "hot_takes": [],
    "decisions": [],
    "open_questions": [],
    "action_items": [],
    "mentions_of_user": [],
    "links": [],
}

PASS1_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "key_participants": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "summary", "key_participants"],
            },
        },
        "hot_takes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "person": {"type": "string"},
                    "stance": {"type": "string"},
                    "topic": {"type": "string"},
                    "short_quote": {"type": "string"},
                },
                "required": ["person", "stance", "topic", "short_quote"],
            },
        },
        "decisions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "owner": {"type": ["string", "null"]},
                },
                "required": ["task", "owner"],
            },
        },
        "mentions_of_user": {"type": "array", "items": {"type": "string"}},
        "links": {"type": "array", "items": {"type": "string"}},
    },
    "required": list(EMPTY_SUMMARY.keys()),
}

RECORD_SUMMARY_TOOL = {
    "name": "record_summary",
    "description": "Record the structured daily digest for this WhatsApp group.",
    "input_schema": PASS1_SCHEMA,
}


class AnthropicLike(Protocol):
    """Minimal shape of anthropic.Anthropic we depend on, so tests can pass a stub."""

    messages: Any


def format_messages(messages: list[Message], tz: ZoneInfo) -> str:
    lines = []
    for m in messages:
        local_time = m.timestamp_utc.astimezone(tz)
        body = m.text if m.text else f"<{m.type}>"
        line = f"[{local_time:%H:%M}] {m.sender_name}: {body}"
        if m.quoted_text:
            line += f"\n    > {m.quoted_text}"
        lines.append(line)
    return "\n".join(lines)


def chunk_messages(messages: list[Message], char_budget: int = CHUNK_CHAR_BUDGET) -> list[list[Message]]:
    chunks: list[list[Message]] = []
    current: list[Message] = []
    current_len = 0
    for m in messages:
        approx_len = len(m.text or "") + 60
        if current and current_len + approx_len > char_budget:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(m)
        current_len += approx_len
    if current:
        chunks.append(current)
    return chunks


def merge_chunk_results(results: list[dict]) -> dict:
    merged: dict[str, list] = {key: [] for key in EMPTY_SUMMARY}
    for result in results:
        for key in merged:
            merged[key].extend(result.get(key, []))
    for key in ("decisions", "open_questions", "mentions_of_user", "links"):
        merged[key] = list(dict.fromkeys(merged[key]))
    return merged


def _call_pass1(
    client: AnthropicLike,
    settings: Settings,
    group_name: str,
    date_str: str,
    messages_text: str,
) -> tuple[dict, int, int]:
    system = render_prompt("pass1_system.md")
    user = render_prompt(
        "pass1_user.md",
        group_name=group_name,
        date=date_str,
        user_name=settings.user_display_name or "the user",
        messages=messages_text,
    )
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[RECORD_SUMMARY_TOOL],
        tool_choice={"type": "tool", "name": "record_summary"},
    )
    tool_use = next(block for block in response.content if block.type == "tool_use")
    return tool_use.input, response.usage.input_tokens, response.usage.output_tokens


def summarize_day(
    client: AnthropicLike,
    settings: Settings,
    group_name: str,
    date: dt.date,
    messages: list[Message],
) -> tuple[dict, int, int]:
    """Summarize one group's messages for one day. Returns (summary_json, input_tokens, output_tokens)."""
    if not messages:
        return dict(EMPTY_SUMMARY), 0, 0

    tz = ZoneInfo(settings.timezone)
    chunks = chunk_messages(messages)

    results: list[dict] = []
    total_input_tokens = 0
    total_output_tokens = 0
    for chunk in chunks:
        text = format_messages(chunk, tz)
        result, input_tokens, output_tokens = _call_pass1(client, settings, group_name, date.isoformat(), text)
        results.append(result)
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens

    merged = results[0] if len(results) == 1 else merge_chunk_results(results)
    return merged, total_input_tokens, total_output_tokens
