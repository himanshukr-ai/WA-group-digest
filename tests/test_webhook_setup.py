from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.config import Settings
from app.whapi.client import WhapiClient
from app.whapi.webhook_setup import register_webhook

BASE_URL = "https://gate.whapi.cloud"

EXISTING_SETTINGS = {
    "callback_persist": False,
    "webhooks": [
        {
            "url": "https://zendox-production.up.railway.app/api/whatsapp/webhook/abc123",
            "mode": "body",
            "events": [{"type": "messages", "method": "post"}],
        }
    ],
}


def make_settings(**overrides) -> Settings:
    return Settings(whapi_token="test-token", **overrides)


@respx.mock
def test_register_webhook_appends_without_touching_existing_entry():
    settings = make_settings(webhook_base_url="https://digest-bot.example.com")
    respx.get(f"{BASE_URL}/settings").mock(return_value=httpx.Response(200, json=EXISTING_SETTINGS))
    patch_route = respx.patch(f"{BASE_URL}/settings").mock(return_value=httpx.Response(200, json={"ok": True}))

    with WhapiClient(settings) as client:
        result = register_webhook(client, settings)

    assert result == "registered"
    sent = json.loads(patch_route.calls[0].request.content)
    urls = [w["url"] for w in sent["webhooks"]]
    assert "https://zendox-production.up.railway.app/api/whatsapp/webhook/abc123" in urls
    assert "https://digest-bot.example.com/webhook/whapi" in urls
    assert len(sent["webhooks"]) == 2


@respx.mock
def test_register_webhook_is_idempotent():
    settings = make_settings(webhook_base_url="https://digest-bot.example.com")
    already_registered = {
        "webhooks": EXISTING_SETTINGS["webhooks"]
        + [{"url": "https://digest-bot.example.com/webhook/whapi", "mode": "body", "events": []}]
    }
    respx.get(f"{BASE_URL}/settings").mock(return_value=httpx.Response(200, json=already_registered))
    patch_route = respx.patch(f"{BASE_URL}/settings").mock(return_value=httpx.Response(200, json={"ok": True}))

    with WhapiClient(settings) as client:
        result = register_webhook(client, settings)

    assert result == "already_registered"
    assert patch_route.call_count == 0


def test_register_webhook_requires_webhook_base_url():
    settings = make_settings(webhook_base_url="")
    with pytest.raises(ValueError):
        register_webhook(client=None, settings=settings)
