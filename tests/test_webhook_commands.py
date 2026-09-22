from __future__ import annotations

import json

import httpx
import respx
from fastapi.testclient import TestClient

from app.config import get_settings

BASE_URL = "https://gate.whapi.cloud"


def _self_chat_payload(text: str, self_chat_id: str, from_me: bool = True, msg_id: str = "cmd1") -> dict:
    return {
        "messages": [
            {
                "id": msg_id,
                "from_me": from_me,
                "type": "text",
                "chat_id": self_chat_id,
                "timestamp": 1712995600,
                "source": "mobile",
                "text": {"body": text},
                "from": "971500000000",
                "from_name": "Himanshu",
            }
        ],
        "event": {"type": "messages", "event": "post"},
        "channel_id": "MANTIS-M72HC",
    }


@respx.mock
def test_groups_command_replies_via_self_chat(db_env, monkeypatch):
    monkeypatch.setenv("SELF_NUMBER", "971500000000")

    send_route = respx.post(f"{BASE_URL}/messages/text").mock(
        return_value=httpx.Response(200, json={"sent": True, "message": {}})
    )

    from app.api import app

    with TestClient(app) as client:
        settings = get_settings()
        payload = _self_chat_payload("/groups", settings.self_chat_id)
        response = client.post("/webhook/whapi", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body == {"ingested": 0, "skipped": 0, "commands_handled": 1}

    assert send_route.call_count == 1
    sent_body = json.loads(send_route.calls[0].request.content)
    assert sent_body["to"] == settings.self_chat_id
    assert "Site A Coordination" in sent_body["body"]


@respx.mock
def test_self_chat_message_not_from_me_is_ignored(db_env, monkeypatch):
    monkeypatch.setenv("SELF_NUMBER", "971500000000")

    send_route = respx.post(f"{BASE_URL}/messages/text").mock(
        return_value=httpx.Response(200, json={"sent": True, "message": {}})
    )

    from app.api import app

    with TestClient(app) as client:
        settings = get_settings()
        payload = _self_chat_payload("/groups", settings.self_chat_id, from_me=False)
        response = client.post("/webhook/whapi", json=payload)

    assert response.json() == {"ingested": 0, "skipped": 1, "commands_handled": 0}
    assert send_route.call_count == 0


@respx.mock
def test_plain_self_chat_text_is_not_treated_as_command(db_env, monkeypatch):
    monkeypatch.setenv("SELF_NUMBER", "971500000000")

    send_route = respx.post(f"{BASE_URL}/messages/text").mock(
        return_value=httpx.Response(200, json={"sent": True, "message": {}})
    )

    from app.api import app

    with TestClient(app) as client:
        settings = get_settings()
        payload = _self_chat_payload("just a note to self", settings.self_chat_id)
        response = client.post("/webhook/whapi", json=payload)

    assert response.json() == {"ingested": 0, "skipped": 1, "commands_handled": 0}
    assert send_route.call_count == 0


def test_group_ingest_is_unaffected_when_self_number_is_unset(app_client):
    from tests.conftest import load_fixture

    payload = load_fixture("webhook_text_message.json")
    response = app_client.post("/webhook/whapi", json=payload)
    assert response.json() == {"ingested": 1, "skipped": 0, "commands_handled": 0}
