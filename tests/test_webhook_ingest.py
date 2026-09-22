from __future__ import annotations

from app.db.models import Message
from app.db.session import session_scope
from tests.conftest import load_fixture


def test_text_message_is_persisted(app_client):
    payload = load_fixture("webhook_text_message.json")
    response = app_client.post("/webhook/whapi", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ingested": 1, "skipped": 0}

    with session_scope() as session:
        row = session.get(Message, "p.w30M7fgwWD4XwHu.g4CA-gBgTwl0rVw")
        assert row is not None
        assert row.group_id == "120363000000000001@g.us"
        assert row.sender_name == "Gerald"
        assert row.text == "Hello world"


def test_duplicate_message_id_is_not_reinserted(app_client):
    payload = load_fixture("webhook_text_message.json")
    app_client.post("/webhook/whapi", json=payload)
    second_response = app_client.post("/webhook/whapi", json=payload)

    assert second_response.json() == {"ingested": 0, "skipped": 1}

    with session_scope() as session:
        count = session.query(Message).count()
        assert count == 1


def test_quoted_reply_stores_quoted_text(app_client):
    payload = load_fixture("webhook_quoted_reply.json")
    app_client.post("/webhook/whapi", json=payload)

    with session_scope() as session:
        row = session.get(Message, "p.reply001")
        assert row is not None
        assert row.quoted_message_id == "p.w30M7fgwWD4XwHu.g4CA-gBgTwl0rVw"
        assert row.quoted_text == "Hello world"


def test_lid_sender_falls_back_to_display_name(app_client):
    payload = load_fixture("webhook_lid_sender.json")
    app_client.post("/webhook/whapi", json=payload)

    with session_scope() as session:
        row = session.get(Message, "p.lid001")
        assert row is not None
        assert row.sender_id == "1524746986546@lid"
        assert row.sender_name == "Anonymous Admin"


def test_unwatched_group_is_skipped(app_client):
    payload = load_fixture("webhook_unwatched_group.json")
    response = app_client.post("/webhook/whapi", json=payload)

    assert response.json() == {"ingested": 0, "skipped": 1}

    with session_scope() as session:
        row = session.get(Message, "p.unwatched001")
        assert row is None


def test_healthz(app_client):
    response = app_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
