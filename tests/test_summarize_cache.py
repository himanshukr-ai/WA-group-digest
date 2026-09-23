from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db.models import DailySummary
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


def _response():
    return FakeResponse(
        content=[FakeToolUseBlock(input=CANNED_SUMMARY)], usage=FakeUsage(input_tokens=10, output_tokens=5)
    )


def test_todays_summary_is_never_cached(db_env):
    init_db()
    settings = get_settings()
    today = dt.datetime.now(TZ).date()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
        group = settings.load_groups()[0]
        for m in load_construction_group_messages(today, TZ, group_id=group.id, group_name=group.name):
            session.add(m)

    client = FakeAnthropicClient([_response(), _response()])
    with session_scope() as session:
        group = settings.load_groups()[0]
        _, cached_1 = get_or_build_daily_summary(session, client, settings, group, today)
        _, cached_2 = get_or_build_daily_summary(session, client, settings, group, today)

    # the day is still in progress, so both calls rebuild and nothing is stored
    assert (cached_1, cached_2) == (False, False)
    assert len(client.messages.calls) == 2
    with session_scope() as session:
        assert session.query(DailySummary).count() == 0


def test_partial_row_cached_before_its_day_ended_is_rebuilt(db_env):
    init_db()
    settings = get_settings()
    day = dt.date(2026, 9, 20)
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
        group = settings.load_groups()[0]
        for m in load_construction_group_messages(day, TZ, group_id=group.id, group_name=group.name):
            session.add(m)
        # Simulates a row written mid-day by the old behaviour: created during the day itself.
        session.add(
            DailySummary(
                group_id=group.id, summary_date=day, summary_json={"stale": True}, model="m",
                input_tokens=1, output_tokens=1,
                created_at=dt.datetime(2026, 9, 20, 4, 0, tzinfo=dt.timezone.utc),
            )
        )

    client = FakeAnthropicClient([_response()])
    with session_scope() as session:
        group = settings.load_groups()[0]
        row, was_cached = get_or_build_daily_summary(session, client, settings, group, day)

    assert was_cached is False
    assert row.summary_json == CANNED_SUMMARY
    with session_scope() as session:
        rows = session.query(DailySummary).all()
        assert len(rows) == 1 and rows[0].summary_json == CANNED_SUMMARY
