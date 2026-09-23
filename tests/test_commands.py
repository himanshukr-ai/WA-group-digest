from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.db.session import init_db, session_scope
from app.db.sync_groups import sync_groups
from app.delivery.commands import ParsedCommand, handle_command, is_self_command, parse_command
from app.whapi.schemas import TextBody, WhapiMessage
from tests.conftest import load_construction_group_messages
from tests.fake_anthropic import FakeAnthropicClient, FakeResponse, FakeTextBlock, FakeToolUseBlock, FakeUsage

PASS1_SUMMARY = {
    "topics": [],
    "hot_takes": [],
    "decisions": ["Some decision"],
    "open_questions": [],
    "action_items": [],
    "mentions_of_user": [],
    "links": [],
}


def test_parse_command_basic():
    cmd = parse_command("/digest 3d Site A")
    assert cmd == ParsedCommand(name="digest", args=["3d", "Site", "A"])


def test_parse_command_ignores_non_commands():
    assert parse_command("hello there") is None
    assert parse_command("/") is None


def test_is_self_command_requires_self_chat_and_from_me():
    settings = Settings(self_number="971500000000")
    msg = WhapiMessage(
        id="1", from_me=True, type="text", chat_id=settings.self_chat_id, timestamp=1700000000,
        text=TextBody(body="/groups"),
    )
    assert is_self_command(msg, settings) is True

    msg_other_chat = msg.model_copy(update={"chat_id": "999@g.us"})
    assert is_self_command(msg_other_chat, settings) is False

    msg_not_from_me = msg.model_copy(update={"from_me": False})
    assert is_self_command(msg_not_from_me, settings) is False

    msg_no_self_number = msg.model_copy()
    assert is_self_command(msg_no_self_number, Settings(self_number="")) is False


def test_handle_command_groups_lists_watchlist(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
    with session_scope() as session:
        reply = handle_command(session, None, settings, ParsedCommand(name="groups"))
    assert "Site A Coordination" in reply


def test_handle_command_digest_missing_window_shows_usage(db_env):
    settings = get_settings()
    reply = handle_command(None, None, settings, ParsedCommand(name="digest"))
    assert "Usage" in reply


def test_handle_command_digest_without_anthropic_key_reports_it(db_env):
    settings = get_settings()
    reply = handle_command(None, None, settings, ParsedCommand(name="digest", args=["1d"]))
    assert "ANTHROPIC_API_KEY" in reply


def test_handle_command_digest_builds_and_returns_digest(db_env):
    init_db()
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
        group = settings.load_groups()[0]
        today = dt.datetime.now(tz).date()
        for m in load_construction_group_messages(today, tz, group_id=group.id, group_name=group.name):
            session.add(m)

    client = FakeAnthropicClient(
        [
            FakeResponse(content=[FakeToolUseBlock(input=PASS1_SUMMARY)], usage=FakeUsage(input_tokens=1, output_tokens=1)),
            FakeResponse(content=[FakeTextBlock(text="DIGEST TEXT")], usage=FakeUsage(input_tokens=1, output_tokens=1)),
        ]
    )

    with session_scope() as session:
        reply = handle_command(session, client, settings, ParsedCommand(name="digest", args=["1d"]))

    assert reply == "DIGEST TEXT"


def test_handle_command_unknown_returns_help(db_env):
    settings = get_settings()
    reply = handle_command(None, None, settings, ParsedCommand(name="foo"))
    assert "Unknown command" in reply
