from __future__ import annotations

from unittest.mock import patch

from app.api import is_local_client


def test_is_local_client_allows_localhost_and_testclient():
    assert is_local_client("127.0.0.1") is True
    assert is_local_client("::1") is True
    assert is_local_client("testclient") is True  # Starlette's TestClient reports this as its host
    assert is_local_client("203.0.113.5") is False
    assert is_local_client(None) is False


def test_admin_run_digest_triggers_when_local(app_client):
    with patch("app.delivery.scheduler.run_daily_digest") as mock_run:
        response = app_client.post("/admin/run-digest")

    assert response.status_code == 200
    assert response.json() == {"status": "triggered"}
    mock_run.assert_called_once()


def test_admin_run_digest_rejects_non_local_client(app_client, monkeypatch):
    monkeypatch.setattr("app.api.LOCAL_CLIENTS", set())  # nothing counts as local now

    response = app_client.post("/admin/run-digest")

    assert response.status_code == 403
