from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db.session import init_db, session_scope
from app.db.sync_groups import sync_groups
from app.summarize.cache import get_or_build_daily_summary
from tests.conftest import load_construction_group_messages
from tests.fake_anthropic import FakeAnthropicClient, FakeResponse, FakeToolUseBlock, FakeUsage

TZ = ZoneInfo("Asia/Dubai")
DAY = dt.date(2026, 9, 20)

CANNED_SUMMARY = {
    "topics": [],
    "hot_takes": [],
    "decisions": ["Switch supplier"],
    "open_questions": [],
    "action_items": [],
    "mentions_of_user": [],
    "links": [],
}


def test_second_call_hits_cache_and_skips_the_client(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
        group = settings.load_groups()[0]
        for m in load_construction_group_messages(DAY, TZ, group_id=group.id, group_name=group.name):
            session.add(m)

    client = FakeAnthropicClient(
        [FakeResponse(content=[FakeToolUseBlock(input=CANNED_SUMMARY)], usage=FakeUsage(input_tokens=900, output_tokens=150))]
    )

    with session_scope() as session:
        group = settings.load_groups()[0]
        row1, was_cached_1 = get_or_build_daily_summary(session, client, settings, group, DAY)
        assert was_cached_1 is False
        assert row1.summary_json == CANNED_SUMMARY
        assert row1.input_tokens == 900
        assert row1.output_tokens == 150

    with session_scope() as session:
        group = settings.load_groups()[0]
        row2, was_cached_2 = get_or_build_daily_summary(session, client, settings, group, DAY)
        assert was_cached_2 is True
        assert row2.summary_json == CANNED_SUMMARY

    # only the first call should have hit the (fake) model
    assert len(client.messages.calls) == 1
