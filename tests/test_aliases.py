from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db.models import Message
from app.db.session import init_db, session_scope
from app.db.sync_groups import sync_groups
from app.summarize.aliases import Pseudonymizer, is_number_like
from app.summarize.pass1 import format_messages
from app.summarize.pass2 import build_digest
from tests.fake_anthropic import FakeAnthropicClient, FakeResponse, FakeTextBlock, FakeToolUseBlock, FakeUsage

TZ = ZoneInfo("Asia/Dubai")
DAY = dt.date(2026, 9, 20)


def test_number_like_detection():
    for name in (None, "", "  ", "Unknown", "919742332963", "+971 58 594 9007", "+1 (604) 790-9652"):
        assert is_number_like(name), name
    for name in ("Gerald", "Khalid (PM)", "Anonymous Admin", "Dr. 12"):
        assert not is_number_like(name), name


def test_labels_are_sequential_and_stable_across_instances(db_env):
    init_db()
    with session_scope() as session:
        p = Pseudonymizer(session, "SMM")
        assert p.label_for("919742332963", "919742332963") == "SMM1"
        assert p.label_for("16047909652", "16047909652") == "SMM2"
        assert p.label_for("919742332963", "919742332963") == "SMM1"

    with session_scope() as session:  # a fresh instance sees the persisted labels
        p = Pseudonymizer(session, "SMM")
        assert p.label_for("919742332963", None) == "SMM1"
        assert p.label_for("17324296240", "") == "SMM3"  # numbering continues


def test_real_names_are_kept_and_number_only_variants_are_labelled(db_env):
    init_db()
    with session_scope() as session:
        p = Pseudonymizer(session, "SMM")
        assert p.label_for("919984351847", "Gerald") == "Gerald"
        assert p.label_for("1524746986546@lid", "Unknown") == "SMM1"  # hidden-number member
        assert p.label_for("919812345678", "+91 98123 45678") == "SMM2"  # name is just a number
        assert p.label_for("", "Unknown") == "Unknown"  # nothing to key a label on


def test_a_known_real_name_wins_over_a_label_for_the_same_person(db_env):
    init_db()
    with session_scope() as session:
        # The same sender shows up number-only on one message and with a name on another.
        session.add(_msg(0, "16047909652", "16047909652", "first message, no name resolved"))
        session.add(_msg(1, "16047909652", "Nikhil Wason", "later message, named"))
        session.add(_msg(2, "917022155324", "917022155324", "someone we never learn the name of"))

    with session_scope() as session:
        p = Pseudonymizer(session, "SMM")
        assert p.label_for("16047909652", "16047909652") == "Nikhil Wason"
        assert p.label_for("917022155324", "917022155324") == "SMM1"  # no name known: a label
        assert p.scrub("ping 16047909652 and @16047909652") == "ping Nikhil Wason and @Nikhil Wason"


def test_label_handed_out_before_the_name_was_known_is_renamed_in_output(db_env):
    init_db()
    with session_scope() as session:
        # Older cached summaries say SMM1; we have since learned who SMM1 is.
        p = Pseudonymizer(session, "SMM")
        assert p.label_for("16047909652", "16047909652") == "SMM1"
        session.add(_msg(0, "16047909652", "Nikhil Wason", "hi"))
        session.flush()

    with session_scope() as session:
        p = Pseudonymizer(session, "SMM")
        assert p.scrub("SMM1 said hi. SMM10 and XSMM1 are unrelated.") == "Nikhil Wason said hi. SMM10 and XSMM1 are unrelated."


def test_scrub_covers_number_formats_and_mentions(db_env):
    init_db()
    with session_scope() as session:
        p = Pseudonymizer(session, "SMM")
        p.label_for("919742332963", "919742332963")  # SMM1
        text = "Ask 919742332963 or +919742332963 or @919742332963, also +91 97423 32963. Order 1234567890 is late."
        assert p.scrub(text) == "Ask SMM1 or SMM1 or @SMM1, also SMM1. Order 1234567890 is late."

        # An @mention is always a person, so an unknown one gets a label; a bare unknown number doesn't.
        assert p.scrub("@971501112222 ping") == "@SMM2 ping"
        assert p.scrub("@971501112222 again") == "@SMM2 again"
        assert p.scrub("call 971509998888") == "call 971509998888"


def test_disabled_when_prefix_is_empty(db_env):
    init_db()
    with session_scope() as session:
        p = Pseudonymizer(session, "")
        assert p.label_for("919742332963", "919742332963") == "919742332963"
        assert p.scrub("919742332963") == "919742332963"


def test_summary_naming_people_by_number_is_flagged_stale():
    from app.summarize.aliases import summary_has_raw_numbers

    base = {"topics": [], "hot_takes": [], "decisions": [], "open_questions": [], "action_items": [],
            "mentions_of_user": [], "links": []}
    assert summary_has_raw_numbers({**base, "hot_takes": [{"person": "1604790965652"}]})  # garbled copy
    assert summary_has_raw_numbers({**base, "topics": [{"key_participants": ["Gerald", "+971 58 594 9007"]}]})
    assert summary_has_raw_numbers({**base, "action_items": [{"task": "x", "owner": "919742332963"}]})
    # labels, names, null owners, and numbers in free text are all fine
    assert not summary_has_raw_numbers({**base, "hot_takes": [{"person": "SMM4"}, {"person": "Gerald"}]})
    assert not summary_has_raw_numbers({**base, "action_items": [{"task": "PO 1234567890", "owner": None}],
                                        "decisions": ["Order 1234567890 approved"]})


def test_stale_check_tolerates_malformed_cached_summaries():
    from app.summarize.aliases import summary_has_raw_numbers

    assert summary_has_raw_numbers({}) is False
    assert summary_has_raw_numbers({"topics": ["just a string"], "hot_takes": "nope", "action_items": [None, 3]}) is False
    assert summary_has_raw_numbers({"topics": [{"key_participants": "not a list"}], "hot_takes": [{"person": None}]}) is False
    # a valid item alongside malformed ones is still detected
    assert summary_has_raw_numbers({"action_items": ["free text", {"task": "x", "owner": "919742332963"}]}) is True


def test_cached_day_that_names_people_by_number_is_rebuilt(db_env):
    from app.db.models import DailySummary
    from app.summarize.cache import get_or_build_daily_summary

    init_db()
    settings = get_settings()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
        group = settings.load_groups()[0]
        session.add(_msg(0, "919742332963", "919742332963", "hello"))
        session.add(
            DailySummary(
                group_id=group.id, summary_date=DAY, model="m", input_tokens=1, output_tokens=1,
                summary_json={"topics": [], "decisions": [], "open_questions": [], "action_items": [],
                              "mentions_of_user": [], "links": [],
                              "hot_takes": [{"person": "1604790965652", "stance": "s", "topic": "t", "short_quote": "q"}]},
            )
        )

    fresh = {"topics": [], "decisions": [], "open_questions": [], "action_items": [], "mentions_of_user": [],
             "links": [], "hot_takes": [{"person": "SMM1", "stance": "s", "topic": "t", "short_quote": "q"}]}
    client = FakeAnthropicClient(
        [FakeResponse(content=[FakeToolUseBlock(input=fresh)], usage=FakeUsage(input_tokens=1, output_tokens=1))]
    )
    with session_scope() as session:
        row, was_cached = get_or_build_daily_summary(session, client, settings, settings.load_groups()[0], DAY)

    assert was_cached is False and len(client.messages.calls) == 1
    assert row.summary_json["hot_takes"][0]["person"] == "SMM1"
    with session_scope() as session:
        rows = session.query(DailySummary).all()
        assert len(rows) == 1 and rows[0].summary_json["hot_takes"][0]["person"] == "SMM1"


def _msg(i: int, sender_id: str, sender_name: str, text: str, quoted: str | None = None) -> Message:
    return Message(
        message_id=f"m{i}", group_id="120363000000000001@g.us", group_name="Site A Coordination",
        sender_id=sender_id, sender_name=sender_name,
        timestamp_utc=dt.datetime(2026, 9, 20, 8, i, tzinfo=dt.timezone.utc),
        type="text", text=text, quoted_message_id=None, quoted_text=quoted, raw_json={},
    )


def test_format_messages_labels_senders_mentions_and_quotes(db_env):
    init_db()
    messages = [
        _msg(0, "919742332963", "919742332963", "hi @919812345678 please see +919742332963"),
        _msg(1, "919984351847", "Gerald", "on it", quoted="ref 919742332963"),
    ]
    with session_scope() as session:
        out = format_messages(messages, TZ, Pseudonymizer(session, "SMM"))

    assert "SMM1: hi @SMM2 please see SMM1" in out
    assert "Gerald: on it" in out
    assert "> ref SMM1" in out
    assert "9742332963" not in out and "9812345678" not in out


def test_digest_never_shows_raw_numbers_even_from_old_cache_or_model_echo(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
        for m in (
            _msg(0, "919742332963", "919742332963", "I think we should switch suppliers"),
            _msg(1, "919984351847", "Gerald", "agreed"),
        ):
            session.add(m)

    pass1 = {
        "topics": [], "decisions": [], "open_questions": [], "action_items": [], "mentions_of_user": [],
        "links": [],
        # As if an older cached summary, or the model, carried the raw number through.
        "hot_takes": [{"person": "919742332963", "stance": "switch suppliers", "topic": "t", "short_quote": "q"}],
    }
    client = FakeAnthropicClient(
        [
            FakeResponse(content=[FakeToolUseBlock(input=pass1)], usage=FakeUsage(input_tokens=1, output_tokens=1)),
            FakeResponse(content=[FakeTextBlock(text="919742332963 wants to switch suppliers.")], usage=FakeUsage(input_tokens=1, output_tokens=1)),
        ]
    )
    with session_scope() as session:
        digest, _usage = build_digest(session, client, settings, settings.load_groups(), window_days=1, end_date=DAY)
        # tables/labels must be created in the same transaction the digest ran in
        assert session.query(Message).count() == 2

    pass1_prompt = client.messages.calls[0]["messages"][0]["content"]
    pass2_prompt = client.messages.calls[1]["messages"][0]["content"]
    assert "SMM1: I think we should switch suppliers" in pass1_prompt
    assert "919742332963" not in pass1_prompt
    assert "919742332963" not in pass2_prompt and "SMM1" in pass2_prompt
    assert digest == "SMM1 wants to switch suppliers."
