from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.config import get_settings
from app.db.session import init_db, session_scope
from app.db.sync_groups import sync_groups
from app.summarize.pass2 import build_digest, parse_window
from tests.conftest import load_construction_group_messages
from tests.fake_anthropic import FakeAnthropicClient, FakeResponse, FakeTextBlock, FakeToolUseBlock, FakeUsage

TZ = ZoneInfo("Asia/Dubai")
DAY_1 = dt.date(2026, 9, 19)
DAY_2 = dt.date(2026, 9, 20)

PASS1_SUMMARY = {
    "topics": [{"title": "T", "summary": "S", "key_participants": ["Ali"]}],
    "hot_takes": [],
    "decisions": ["Some decision"],
    "open_questions": [],
    "action_items": [],
    "mentions_of_user": [],
    "links": [],
}


@pytest.mark.parametrize(
    "window,expected_days",
    [("1d", 1), ("3d", 3), ("7d", 7), ("5", 5)],
)
def test_parse_window(window, expected_days):
    assert parse_window(window) == expected_days


def test_parse_window_rejects_garbage():
    with pytest.raises(ValueError):
        parse_window("not-a-window")


def _seed_two_days(group):
    with session_scope() as session:
        for m in load_construction_group_messages(DAY_1, TZ, group_id=group.id, group_name=group.name):
            m.message_id = "d1-" + m.message_id
            session.add(m)
        for m in load_construction_group_messages(DAY_2, TZ, group_id=group.id, group_name=group.name):
            m.message_id = "d2-" + m.message_id
            session.add(m)


def test_build_digest_runs_pass1_per_day_then_merges(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
    group = settings.load_groups()[0]
    _seed_two_days(group)

    client = FakeAnthropicClient(
        [
            FakeResponse(content=[FakeToolUseBlock(input=PASS1_SUMMARY)], usage=FakeUsage(input_tokens=500, output_tokens=100)),
            FakeResponse(content=[FakeToolUseBlock(input=PASS1_SUMMARY)], usage=FakeUsage(input_tokens=500, output_tokens=100)),
            FakeResponse(content=[FakeTextBlock(text="THE DIGEST")], usage=FakeUsage(input_tokens=300, output_tokens=200)),
        ]
    )

    with session_scope() as session:
        digest_text, usage = build_digest(
            session, client, settings, settings.load_groups(), window_days=2, end_date=DAY_2
        )

    assert digest_text == "THE DIGEST"
    assert len(client.messages.calls) == 3  # 2x pass1 + 1x pass2
    assert usage == {"input_tokens": 1300, "output_tokens": 400, "cost": usage["cost"]}

    # pass2 prompt should carry both days' summaries as JSON
    pass2_call = client.messages.calls[-1]
    pass2_prompt = pass2_call["messages"][0]["content"]
    assert DAY_1.isoformat() in pass2_prompt
    assert DAY_2.isoformat() in pass2_prompt
    assert "Some decision" in pass2_prompt


def test_build_digest_second_run_only_pays_for_pass2(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
    group = settings.load_groups()[0]
    _seed_two_days(group)

    first_client = FakeAnthropicClient(
        [
            FakeResponse(content=[FakeToolUseBlock(input=PASS1_SUMMARY)], usage=FakeUsage(input_tokens=500, output_tokens=100)),
            FakeResponse(content=[FakeToolUseBlock(input=PASS1_SUMMARY)], usage=FakeUsage(input_tokens=500, output_tokens=100)),
            FakeResponse(content=[FakeTextBlock(text="RUN 1")], usage=FakeUsage(input_tokens=300, output_tokens=200)),
        ]
    )
    with session_scope() as session:
        build_digest(session, first_client, settings, settings.load_groups(), window_days=2, end_date=DAY_2)

    second_client = FakeAnthropicClient(
        [FakeResponse(content=[FakeTextBlock(text="RUN 2")], usage=FakeUsage(input_tokens=300, output_tokens=200))]
    )
    with session_scope() as session:
        digest_text, usage = build_digest(
            session, second_client, settings, settings.load_groups(), window_days=2, end_date=DAY_2
        )

    assert digest_text == "RUN 2"
    assert len(second_client.messages.calls) == 1  # cached daily summaries, only the merge call happens
    assert usage["input_tokens"] == 300
    assert usage["output_tokens"] == 200


def test_default_window_is_n_full_days_plus_today_so_far(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
    group = settings.load_groups()[0]

    today = dt.datetime.now(TZ).date()
    with session_scope() as session:
        for label, day in (("y", today - dt.timedelta(days=1)), ("t", today)):
            for m in load_construction_group_messages(day, TZ, group_id=group.id, group_name=group.name):
                m.message_id = f"{label}-{m.message_id}"
                session.add(m)

    client = FakeAnthropicClient(
        [
            FakeResponse(content=[FakeToolUseBlock(input=PASS1_SUMMARY)], usage=FakeUsage(input_tokens=1, output_tokens=1)),
            FakeResponse(content=[FakeToolUseBlock(input=PASS1_SUMMARY)], usage=FakeUsage(input_tokens=1, output_tokens=1)),
            FakeResponse(content=[FakeTextBlock(text="DIGEST")], usage=FakeUsage(input_tokens=1, output_tokens=1)),
        ]
    )
    with session_scope() as session:
        digest_text, _ = build_digest(session, client, settings, settings.load_groups(), window_days=1)

    assert digest_text == "DIGEST"
    assert len(client.messages.calls) == 3  # yesterday + today pass1, then the merge
    pass2_prompt = client.messages.calls[-1]["messages"][0]["content"]
    assert (today - dt.timedelta(days=1)).isoformat() in pass2_prompt
    assert today.isoformat() in pass2_prompt


def test_build_digest_with_no_messages_reports_nothing(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())

    client = FakeAnthropicClient([])
    with session_scope() as session:
        digest_text, usage = build_digest(session, client, settings, settings.load_groups(), window_days=1)

    assert digest_text == "Nothing to report for this window."
    assert client.messages.calls == []
