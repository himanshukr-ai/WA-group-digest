from __future__ import annotations

from app.config import GroupConfig, get_settings
from app.db.models import Group
from app.db.session import init_db, session_scope
from app.db.sync_groups import load_groups_from_db, seed_groups_if_empty


def test_seed_imports_yaml_into_an_empty_watchlist(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        assert seed_groups_if_empty(session, settings.load_groups()) is True
    with session_scope() as session:
        groups = load_groups_from_db(session)
    assert [(g.id, g.name, g.enabled) for g in groups] == [("120363000000000001@g.us", "Site A Coordination", True)]


def test_seed_never_overwrites_edits_made_after_the_first_run(db_env):
    init_db()
    settings = get_settings()
    with session_scope() as session:
        seed_groups_if_empty(session, settings.load_groups())

    # Simulates an edit from the admin page, then a redeploy re-running the seed.
    with session_scope() as session:
        row = session.get(Group, "120363000000000001@g.us")
        row.enabled = False
        session.add(Group(id="extra@g.us", name="Added In UI", enabled=True, notes=""))

    with session_scope() as session:
        assert seed_groups_if_empty(session, settings.load_groups()) is False

    with session_scope() as session:
        by_id = {g.id: g for g in load_groups_from_db(session)}
    assert by_id["120363000000000001@g.us"].enabled is False
    assert by_id["extra@g.us"].name == "Added In UI"


def test_load_groups_from_db_returns_group_configs(db_env):
    init_db()
    with session_scope() as session:
        session.add(Group(id="a@g.us", name="B group", enabled=True, notes=None))
        session.add(Group(id="b@g.us", name="A group", enabled=False, notes="hi"))
    with session_scope() as session:
        groups = load_groups_from_db(session)
    assert all(isinstance(g, GroupConfig) for g in groups)
    assert [g.name for g in groups] == ["A group", "B group"]  # ordered by name
    assert groups[1].notes == ""  # NULL notes become an empty string
