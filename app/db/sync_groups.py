from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import GroupConfig
from app.db.models import Group


def sync_groups(session: Session, groups: list[GroupConfig]) -> None:
    """Upsert the given groups into the groups table, overwriting name/enabled/notes."""
    for g in groups:
        row = session.get(Group, g.id)
        if row is None:
            session.add(Group(id=g.id, name=g.name, enabled=g.enabled, notes=g.notes))
        else:
            row.name = g.name
            row.enabled = g.enabled
            row.notes = g.notes


def seed_groups_if_empty(session: Session, groups: list[GroupConfig]) -> bool:
    """Import groups.yaml entries, but only into an empty watchlist.

    The database is the source of truth (the admin page edits it). groups.yaml is baked into the
    deployed image, so re-importing it on every start would silently undo those edits.
    Returns True if the seed ran.
    """
    if session.execute(select(Group.id).limit(1)).first() is not None:
        return False
    for g in groups:
        session.add(Group(id=g.id, name=g.name, enabled=g.enabled, notes=g.notes))
    return True


def load_groups_from_db(session: Session) -> list[GroupConfig]:
    rows = session.execute(select(Group).order_by(Group.name)).scalars()
    return [GroupConfig(id=r.id, name=r.name, enabled=r.enabled, notes=r.notes or "") for r in rows]
