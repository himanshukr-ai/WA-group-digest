from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

import httpx
import respx

from app.config import get_settings
from app.db.session import init_db, session_scope
from app.db.sync_groups import sync_groups
from app.delivery.scheduler import run_daily_digest
from tests.conftest import load_construction_group_messages
from tests.fake_anthropic import FakeAnthropicClient, FakeResponse, FakeTextBlock, FakeToolUseBlock, FakeUsage

BASE_URL = "https://gate.whapi.cloud"

PASS1_SUMMARY = {
    "topics": [],
    "hot_takes": [],
    "decisions": ["Some decision"],
    "open_questions": [],
    "action_items": [],
    "mentions_of_user": [],
    "links": [],
}


@respx.mock
def test_run_daily_digest_sends_to_self_chat(db_env, monkeypatch):
    monkeypatch.setenv("SELF_NUMBER", "971500000000")

    init_db()
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
        group = settings.load_groups()[0]
        today = dt.datetime.now(tz).date()
        for m in load_construction_group_messages(today, tz, group_id=group.id, group_name=group.name):
            session.add(m)

    fake_client = FakeAnthropicClient(
        [
            FakeResponse(content=[FakeToolUseBlock(input=PASS1_SUMMARY)], usage=FakeUsage(input_tokens=500, output_tokens=100)),
            FakeResponse(content=[FakeTextBlock(text="DAILY DIGEST")], usage=FakeUsage(input_tokens=200, output_tokens=150)),
        ]
    )
    send_route = respx.post(f"{BASE_URL}/messages/text").mock(
        return_value=httpx.Response(200, json={"sent": True, "message": {}})
    )

    run_daily_digest(settings, anthropic_client=fake_client)

    assert send_route.call_count == 1
    sent_body = json.loads(send_route.calls[0].request.content)
    assert sent_body["to"] == settings.self_chat_id
    assert sent_body["body"] == "DAILY DIGEST"


def test_run_daily_digest_skips_when_self_number_missing(db_env):
    settings = get_settings()
    assert settings.self_chat_id == ""
    # No respx mock registered: if this tried to hit the network it would raise.
    run_daily_digest(settings, anthropic_client=FakeAnthropicClient([]))


def test_run_daily_digest_skips_when_no_api_key_and_no_client(db_env, monkeypatch):
    monkeypatch.setenv("SELF_NUMBER", "971500000000")
    settings = get_settings()
    run_daily_digest(settings, anthropic_client=None)
