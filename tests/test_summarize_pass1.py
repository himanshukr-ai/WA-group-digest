from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db.models import Message
from app.summarize.pass1 import EMPTY_SUMMARY, chunk_messages, summarize_day
from tests.conftest import load_construction_group_messages
from tests.fake_anthropic import FakeAnthropicClient, FakeResponse, FakeToolUseBlock, FakeUsage

TZ = ZoneInfo("Asia/Dubai")
DAY = dt.date(2026, 9, 20)

CANNED_SUMMARY = {
    "topics": [
        {
            "title": "Cement delivery delay",
            "summary": "Cement delivery delayed again; group debated the cause and agreed to switch suppliers.",
            "key_participants": ["Sara (Procurement)", "Ali (Site Eng)", "Khalid (PM)"],
        },
        {
            "title": "Scaffolding safety incident",
            "summary": "A worker slipped near level-3 scaffolding, minor injury.",
            "key_participants": ["Ali (Site Eng)", "Fatima (Site Eng)"],
        },
    ],
    "hot_takes": [
        {
            "person": "Sara (Procurement)",
            "stance": "The supplier should be dropped, this is a pattern not a one-off.",
            "topic": "Cement delivery delay",
            "short_quote": "we've had this exact supplier miss two other deliveries this quarter",
        },
        {
            "person": "Ali (Site Eng)",
            "stance": "The delay is a customs issue, not the supplier's fault.",
            "topic": "Cement delivery delay",
            "short_quote": "This is a customs clearance issue, not the supplier's fault",
        },
    ],
    "decisions": ["Switch to the backup cement supplier starting Monday.", "Client agreed to a 2-week extension."],
    "open_questions": ["Should the scaffolding toolbox talk be redone given the second slip this month?"],
    "action_items": [
        {"task": "Issue PO for backup cement supplier", "owner": "Sara (Procurement)"},
        {"task": "Loop in Himanshu on cost implications of the extension before Thursday", "owner": None},
    ],
    "mentions_of_user": ["Can someone loop in Himanshu on the cost implications of the 2-week extension before Thursday?"],
    "links": ["https://example.com/site-photos/2026-09-20"],
}


def make_settings(**overrides) -> Settings:
    return Settings(
        anthropic_api_key="test",
        anthropic_model="claude-sonnet-5",
        timezone="Asia/Dubai",
        user_display_name="Himanshu",
        **overrides,
    )


def test_pass1_prompt_includes_fixture_content_and_returns_canned_summary():
    messages = load_construction_group_messages(DAY, TZ)
    client = FakeAnthropicClient(
        [FakeResponse(content=[FakeToolUseBlock(input=CANNED_SUMMARY)], usage=FakeUsage(input_tokens=1500, output_tokens=400))]
    )

    result, input_tokens, output_tokens = summarize_day(client, make_settings(), "Site A Coordination", DAY, messages)

    assert result == CANNED_SUMMARY
    assert input_tokens == 1500
    assert output_tokens == 400
    assert len(client.messages.calls) == 1

    prompt_text = client.messages.calls[0]["messages"][0]["content"]
    assert "scaffolding" in prompt_text
    assert "cement" in prompt_text
    assert "Himanshu" in prompt_text
    assert "Khalid (PM)" in prompt_text
    assert "https://example.com/site-photos/2026-09-20" in prompt_text


def test_pass1_empty_day_returns_empty_without_calling_client():
    client = FakeAnthropicClient([])

    result, input_tokens, output_tokens = summarize_day(client, make_settings(), "Site A Coordination", DAY, [])

    assert result == EMPTY_SUMMARY
    assert input_tokens == 0
    assert output_tokens == 0
    assert client.messages.calls == []


def test_pass1_chunks_large_day_and_merges_results():
    long_text = "x" * 500
    messages = [
        Message(
            message_id=f"long-{i}",
            group_id="g",
            group_name="Big Group",
            sender_id="Someone",
            sender_name="Someone",
            timestamp_utc=dt.datetime(2026, 9, 20, 8, 0, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=i),
            type="text",
            text=long_text,
            quoted_message_id=None,
            quoted_text=None,
            raw_json={},
        )
        for i in range(40)
    ]
    assert len(chunk_messages(messages)) > 1

    chunk_1_summary = {**EMPTY_SUMMARY, "decisions": ["Decision A"], "links": ["https://a.example"]}
    chunk_2_summary = {**EMPTY_SUMMARY, "decisions": ["Decision A", "Decision B"], "links": ["https://b.example"]}
    client = FakeAnthropicClient(
        [
            FakeResponse(content=[FakeToolUseBlock(input=chunk_1_summary)], usage=FakeUsage(input_tokens=1000, output_tokens=100)),
            FakeResponse(content=[FakeToolUseBlock(input=chunk_2_summary)], usage=FakeUsage(input_tokens=1000, output_tokens=100)),
        ]
    )

    result, input_tokens, output_tokens = summarize_day(client, make_settings(), "Big Group", DAY, messages)

    assert len(client.messages.calls) == 2
    assert input_tokens == 2000
    assert output_tokens == 200
    # merged + de-duped
    assert result["decisions"] == ["Decision A", "Decision B"]
    assert result["links"] == ["https://a.example", "https://b.example"]
