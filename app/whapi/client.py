from __future__ import annotations

import logging
import time

import httpx

from app.config import Settings
from app.whapi.schemas import GroupsList, MessagesList

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


class WhapiClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.whapi_base_url,
            headers={"Authorization": f"Bearer {settings.whapi_token}"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WhapiClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_exc = exc
                self._sleep_backoff(attempt)
                continue

            if response.status_code in RETRY_STATUS_CODES and attempt < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else self._backoff_delay(attempt)
                logger.warning(
                    "Whapi request %s %s got %s, retrying in %.1fs (attempt %d/%d)",
                    method, path, response.status_code, delay, attempt, MAX_RETRIES,
                )
                time.sleep(delay)
                continue

            response.raise_for_status()
            return response

        if last_exc:
            raise last_exc
        raise RuntimeError(f"Whapi request {method} {path} failed after {MAX_RETRIES} retries")

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        return min(2 ** attempt, 30)

    def _sleep_backoff(self, attempt: int) -> None:
        time.sleep(self._backoff_delay(attempt))

    def get_groups(self, count: int = 100, offset: int = 0) -> GroupsList:
        response = self._request("GET", "/groups", params={"count": count, "offset": offset})
        return GroupsList.model_validate(response.json())

    def get_messages(
        self,
        chat_id: str,
        count: int = 100,
        offset: int = 0,
        time_from: int | None = None,
        time_to: int | None = None,
        sort: str = "desc",
    ) -> MessagesList:
        params: dict = {"count": count, "offset": offset, "sort": sort}
        if time_from is not None:
            params["time_from"] = time_from
        if time_to is not None:
            params["time_to"] = time_to
        response = self._request("GET", f"/messages/list/{chat_id}", params=params)
        return MessagesList.model_validate(response.json())

    def send_text(self, to: str, body: str) -> dict:
        response = self._request("POST", "/messages/text", json={"to": to, "body": body})
        return response.json()
