from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.config as config_module
import app.db.session as session_module

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


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
def app_client(tmp_path: Path, watched_groups_yaml: Path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("GROUPS_CONFIG_PATH", str(watched_groups_yaml))
    monkeypatch.setenv("WHAPI_TOKEN", "test-token")

    config_module.get_settings.cache_clear()
    session_module._engine = None
    session_module._SessionLocal = None

    from app.api import app

    with TestClient(app) as client:
        yield client

    config_module.get_settings.cache_clear()
    session_module._engine = None
    session_module._SessionLocal = None
