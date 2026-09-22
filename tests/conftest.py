from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import app.config as config_module
import app.db.session as session_module
from app.db.models import Message

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def load_construction_group_messages(
    date: dt.date, tz: ZoneInfo, group_id: str = "120363000000000001@g.us", group_name: str = "Site A Coordination"
) -> list[Message]:
    """Build (unpersisted) Message rows from the synthetic UAE construction-project fixture."""
    entries = load_fixture("construction_group_day.json")
    messages = []
    for i, entry in enumerate(entries):
        hh, mm = (int(part) for part in entry["time"].split(":"))
        local_dt = dt.datetime.combine(date, dt.time(hh, mm), tzinfo=tz)
        messages.append(
            Message(
                message_id=f"construction-{i}",
                group_id=group_id,
                group_name=group_name,
                sender_id=entry["sender"],
                sender_name=entry["sender"],
                timestamp_utc=local_dt.astimezone(dt.timezone.utc),
                type="text",
                text=entry["text"],
                quoted_message_id=None,
                quoted_text=None,
                raw_json={},
            )
        )
    return messages


@pytest.fixture
def watched_groups_yaml(tmp_path: Path) -> Path:
    content = """
groups:
  - id: "120363000000000001@g.us"
    name: "Site A Coordination"
    enabled: true
    notes: "test group"
"""
    path = tmp_path / "groups.yaml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def db_env(tmp_path: Path, watched_groups_yaml: Path, monkeypatch):
    """Point Settings/the DB at isolated per-test paths. Yields the settings module for convenience."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("GROUPS_CONFIG_PATH", str(watched_groups_yaml))
    monkeypatch.setenv("WHAPI_TOKEN", "test-token")

    config_module.get_settings.cache_clear()
    session_module._engine = None
    session_module._SessionLocal = None

    yield config_module

    config_module.get_settings.cache_clear()
    session_module._engine = None
    session_module._SessionLocal = None


@pytest.fixture
def app_client(db_env):
    from app.api import app

    with TestClient(app) as client:
        yield client
