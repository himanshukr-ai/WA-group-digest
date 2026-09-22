from __future__ import annotations

import logging

from app.config import Settings
from app.whapi.client import WhapiClient

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook/whapi"


def register_webhook(client: WhapiClient, settings: Settings) -> str:
    """Append this app's webhook to the channel's webhooks list.

    A Whapi channel may already be shared with other apps (each with their own webhook
    entry) -- this only ever appends or updates our own entry by URL match, and never
    replaces or drops anyone else's. Returns "registered" or "already_registered".
    """
    if not settings.webhook_base_url:
        raise ValueError("WEBHOOK_BASE_URL is not set")

    target_url = settings.webhook_base_url.rstrip("/") + WEBHOOK_PATH
    current = client.get_channel_settings()
    webhooks = list(current.get("webhooks", []))

    for entry in webhooks:
        if entry.get("url") == target_url:
            return "already_registered"

    webhooks.append(
        {
            "url": target_url,
            "mode": "body",
            "events": [{"type": "messages", "method": "post"}],
        }
    )
    client.update_channel_settings({"webhooks": webhooks})
    logger.info("Registered Whapi webhook: %s", target_url)
    return "registered"
