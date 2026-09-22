from __future__ import annotations

import datetime as dt

from app.config import get_settings
from app.db.models import DailySummary, Message
from app.db.retention import purge_old_messages
from app.db.session import init_db, session_scope
from app.db.sync_groups import sync_groups


def test_purge_old_messages_deletes_only_rows_past_retention(db_env, monkeypatch):
    monkeypatch.setenv("RETENTION_DAYS", "30")
    init_db()
    settings = get_settings()

    now = dt.datetime.now(dt.timezone.utc)
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
        group = settings.load_groups()[0]

        session.add(
            Message(
                message_id="old", group_id=group.id, group_name=group.name, sender_id="s", sender_name="S",
                timestamp_utc=now - dt.timedelta(days=45), type="text", text="old message",
                quoted_message_id=None, quoted_text=None, raw_json={},
            )
        )
        session.add(
            Message(
                message_id="recent", group_id=group.id, group_name=group.name, sender_id="s", sender_name="S",
                timestamp_utc=now - dt.timedelta(days=5), type="text", text="recent message",
                quoted_message_id=None, quoted_text=None, raw_json={},
            )
        )
        session.add(
            DailySummary(
                group_id=group.id, summary_date=(now - dt.timedelta(days=45)).date(),
                summary_json={"topics": []}, model="claude-sonnet-5", input_tokens=1, output_tokens=1,
            )
        )

    with session_scope() as session:
        deleted = purge_old_messages(session, settings)

    assert deleted == 1

    with session_scope() as session:
        assert session.get(Message, "old") is None
        assert session.get(Message, "recent") is not None
        # Summaries are never touched by retention, even for dates past the window.
        assert session.query(DailySummary).count() == 1


def test_purge_old_messages_noop_when_nothing_is_old(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())

    with session_scope() as session:
        deleted = purge_old_messages(session, settings)

    assert deleted == 0
