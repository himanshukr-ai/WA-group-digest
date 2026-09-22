from __future__ import annotations

import httpx
import respx

from app.config import get_settings
from app.db.models import Message
from app.db.session import init_db, session_scope
from app.db.sync_groups import sync_groups
from app.ingest.backfill import backfill_groups
from app.whapi.client import WhapiClient

GROUP_ID = "120363000000000001@g.us"
BASE_URL = "https://gate.whapi.cloud"


def _message(msg_id: str, timestamp: int, body: str) -> dict:
    return {
        "id": msg_id,
        "from_me": False,
        "type": "text",
        "chat_id": GROUP_ID,
        "chat_name": "Site A Coordination",
        "timestamp": timestamp,
        "source": "mobile",
        "text": {"body": body},
        "from": "919984351847",
        "from_name": "Gerald",
    }


@respx.mock
def test_backfill_paginates_and_persists_all_pages(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())

    page_1 = [_message(f"m{i}", 1712990000 + i, f"message {i}") for i in range(200)]
    page_2 = [_message(f"m{i}", 1712990000 + i, f"message {i}") for i in range(200, 250)]

    route = respx.get(f"{BASE_URL}/messages/list/{GROUP_ID}")
    route.side_effect = [
        httpx.Response(200, json={"messages": page_1, "count": 200, "total": 250, "offset": 0}),
        httpx.Response(200, json={"messages": page_2, "count": 50, "total": 250, "offset": 200}),
    ]

    with WhapiClient(settings) as client, session_scope() as session:
        results = backfill_groups(client, session, settings.load_groups(), days=7)

    assert results == {GROUP_ID: 250}
    assert route.call_count == 2

    with session_scope() as session:
        assert session.query(Message).count() == 250


@respx.mock
def test_backfill_skips_already_ingested_messages(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())
        from app.ingest.persist import persist_message
        from app.whapi.schemas import WhapiMessage

        group = settings.load_groups()[0]
        persist_message(session, WhapiMessage.model_validate(_message("m0", 1712990000, "already here")), group)

    page = [_message(f"m{i}", 1712990000 + i, f"message {i}") for i in range(5)]
    route = respx.get(f"{BASE_URL}/messages/list/{GROUP_ID}")
    route.side_effect = [httpx.Response(200, json={"messages": page, "count": 5, "total": 5, "offset": 0})]

    with WhapiClient(settings) as client, session_scope() as session:
        results = backfill_groups(client, session, settings.load_groups(), days=7)

    assert results == {GROUP_ID: 4}

    with session_scope() as session:
        assert session.query(Message).count() == 5


@respx.mock
def test_backfill_skips_disabled_groups(db_env, watched_groups_yaml):
    watched_groups_yaml.write_text(
        """
groups:
  - id: "120363000000000001@g.us"
    name: "Site A Coordination"
    enabled: false
    notes: ""
""",
        encoding="utf-8",
    )
    init_db()
    settings = get_settings()
    route = respx.get(f"{BASE_URL}/messages/list/{GROUP_ID}")

    with WhapiClient(settings) as client, session_scope() as session:
        results = backfill_groups(client, session, settings.load_groups(), days=7)

    assert results == {}
    assert route.call_count == 0
