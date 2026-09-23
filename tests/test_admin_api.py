from __future__ import annotations

import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app.admin.router as admin_router_module
import app.config as config_module
from app.admin.jobs import Job, jobs
from tests.conftest import load_fixture

BASE_URL = "https://gate.whapi.cloud"
GROUP_ID = "120363000000000001@g.us"
AUTH = ("admin", "s3cret-pass")


@pytest.fixture(autouse=True)
def _reset_admin_state():
    jobs.reset()
    admin_router_module._whapi_cache.update(at=0.0, groups=[])
    yield
    jobs.reset()


@pytest.fixture
def admin_client(db_env, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", AUTH[1])
    config_module.get_settings.cache_clear()
    from app.api import app

    with TestClient(app) as client:
        client.auth = AUTH
        yield client


def _whapi_message(msg_id: str, ts: int) -> dict:
    return {
        "id": msg_id, "from_me": False, "type": "text", "chat_id": "new@g.us", "timestamp": ts,
        "text": {"body": f"hello {msg_id}"}, "from": "919984351847", "from_name": "Gerald",
    }


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/admin/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("backfill job did not finish in time")


# --- auth -----------------------------------------------------------------------------------


def test_admin_is_disabled_when_no_password_is_configured(app_client):
    assert app_client.get("/admin").status_code == 404
    assert app_client.get("/admin/api/groups", auth=AUTH).status_code == 404


def test_admin_requires_credentials(admin_client):
    admin_client.auth = None
    response = admin_client.get("/admin/api/groups")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


@pytest.mark.parametrize("creds", [("admin", "wrong"), ("root", AUTH[1]), ("admin", "pässword")])
def test_admin_rejects_bad_credentials(admin_client, creds):
    admin_client.auth = creds
    assert admin_client.get("/admin/api/groups").status_code == 401


def test_unauthenticated_writes_get_401_not_415(admin_client):
    admin_client.auth = None
    response = admin_client.post("/admin/api/groups", content="x", headers={"content-type": "text/plain"})
    assert response.status_code == 401


def test_writes_must_be_json(admin_client):
    response = admin_client.post(
        "/admin/api/groups", content='{"id": "a@g.us", "name": "A"}', headers={"content-type": "text/plain"}
    )
    assert response.status_code == 415


def test_page_is_served_behind_auth(admin_client):
    response = admin_client.get("/admin")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_existing_public_routes_stay_open(admin_client):
    admin_client.auth = None
    assert admin_client.get("/healthz").status_code == 200


# --- watchlist ------------------------------------------------------------------------------


def test_lists_the_seeded_watchlist_with_stats(admin_client):
    groups = admin_client.get("/admin/api/groups").json()
    assert [g["id"] for g in groups] == [GROUP_ID]
    assert groups[0]["message_count"] == 0
    assert groups[0]["last_message_at"] is None


def test_add_update_and_duplicate_group(admin_client):
    created = admin_client.post("/admin/api/groups", json={"id": "new@g.us", "name": " New Group "})
    assert created.status_code == 201
    assert created.json()["name"] == "New Group" and created.json()["enabled"] is True

    assert admin_client.post("/admin/api/groups", json={"id": "new@g.us", "name": "x"}).status_code == 409
    assert admin_client.post("/admin/api/groups", json={"id": "not-a-group", "name": "x"}).status_code == 422

    patched = admin_client.patch("/admin/api/groups/new@g.us", json={"enabled": False, "notes": "site group"})
    assert patched.json()["enabled"] is False and patched.json()["notes"] == "site group"
    assert admin_client.patch("/admin/api/groups/nope@g.us", json={"enabled": False}).status_code == 404


def test_webhook_follows_admin_changes_without_a_restart(admin_client):
    payload = load_fixture("webhook_text_message.json")
    payload["messages"][0]["chat_id"] = "new@g.us"

    unknown = admin_client.post("/webhook/whapi", json=payload, auth=None)
    assert unknown.json()["ingested"] == 0

    admin_client.post("/admin/api/groups", json={"id": "new@g.us", "name": "New"})
    payload["messages"][0]["id"] = "second-message"
    assert admin_client.post("/webhook/whapi", json=payload, auth=None).json()["ingested"] == 1

    admin_client.patch("/admin/api/groups/new@g.us", json={"enabled": False})
    payload["messages"][0]["id"] = "third-message"
    assert admin_client.post("/webhook/whapi", json=payload, auth=None).json()["ingested"] == 0


def test_alias_lookup_lists_labels_in_numeric_order(admin_client):
    from app.db.session import session_scope
    from app.summarize.aliases import Pseudonymizer

    with session_scope() as session:
        p = Pseudonymizer(session, "SMM")
        for i in range(11):
            p.alias_for(f"9715000000{i:02d}")

    aliases = admin_client.get("/admin/api/aliases").json()
    assert [a["label"] for a in aliases] == [f"SMM{i}" for i in range(1, 12)]  # SMM2 before SMM10
    assert aliases[0] == {"label": "SMM1", "number": "971500000000"}


# --- Whapi group picker ---------------------------------------------------------------------


@respx.mock
def test_whapi_groups_are_flagged_sorted_and_cached(admin_client):
    route = respx.get(f"{BASE_URL}/groups").mock(
        return_value=httpx.Response(
            200,
            json={
                "groups": [
                    {"id": "z@g.us", "name": "zebra", "participants_count": 3},
                    {"id": GROUP_ID, "name": "Site A Coordination", "participants_count": 12},
                ],
                "count": 2, "total": 2, "offset": 0,
            },
        )
    )

    groups = admin_client.get("/admin/api/whapi-groups").json()
    assert [g["name"] for g in groups] == ["Site A Coordination", "zebra"]
    assert {g["id"]: g["on_watchlist"] for g in groups} == {GROUP_ID: True, "z@g.us": False}

    admin_client.get("/admin/api/whapi-groups")
    assert route.call_count == 1  # served from the 60s cache
    admin_client.get("/admin/api/whapi-groups?refresh=true")
    assert route.call_count == 2


@respx.mock
def test_whapi_groups_reports_upstream_failure_as_502(admin_client):
    respx.get(f"{BASE_URL}/groups").mock(return_value=httpx.Response(401, json={"error": "bad token"}))
    assert admin_client.get("/admin/api/whapi-groups").status_code == 502


# --- backfill jobs --------------------------------------------------------------------------


@respx.mock
def test_backfill_runs_in_the_background_and_updates_the_listing(admin_client):
    admin_client.post("/admin/api/groups", json={"id": "new@g.us", "name": "New"})
    now = int(time.time())
    respx.get(f"{BASE_URL}/messages/list/new@g.us").mock(
        return_value=httpx.Response(
            200,
            json={"messages": [_whapi_message(f"m{i}", now - i) for i in range(3)], "count": 3, "total": 3, "offset": 0},
        )
    )

    started = admin_client.post("/admin/api/groups/new@g.us/backfill", json={"days": 7})
    assert started.status_code == 202

    job = _wait_for_job(admin_client, started.json()["job_id"])
    assert job["status"] == "done" and job["inserted"] == 3

    listed = {g["id"]: g for g in admin_client.get("/admin/api/groups").json()}["new@g.us"]
    assert listed["message_count"] == 3
    assert listed["backfill"]["status"] == "done"


@respx.mock
def test_backfill_failure_is_reported_on_the_job(admin_client):
    admin_client.post("/admin/api/groups", json={"id": "new@g.us", "name": "New"})
    respx.get(f"{BASE_URL}/messages/list/new@g.us").mock(return_value=httpx.Response(404, json={"error": "nope"}))

    job_id = admin_client.post("/admin/api/groups/new@g.us/backfill", json={"days": 1}).json()["job_id"]
    job = _wait_for_job(admin_client, job_id)
    assert job["status"] == "error"
    assert "404" in job["error"]


def test_second_backfill_for_a_running_group_is_rejected(admin_client):
    jobs._jobs["running-job"] = Job(id="running-job", group_id=GROUP_ID, days=7, started_at="now")
    response = admin_client.post(f"/admin/api/groups/{GROUP_ID}/backfill", json={"days": 7})
    assert response.status_code == 409


@pytest.mark.parametrize("days", [0, 31, -1])
def test_backfill_days_are_bounded(admin_client, days):
    assert admin_client.post(f"/admin/api/groups/{GROUP_ID}/backfill", json={"days": days}).status_code == 422


def test_backfill_unknown_group_and_job(admin_client):
    assert admin_client.post("/admin/api/groups/nope@g.us/backfill", json={"days": 3}).status_code == 404
    assert admin_client.get("/admin/api/jobs/does-not-exist").status_code == 404
