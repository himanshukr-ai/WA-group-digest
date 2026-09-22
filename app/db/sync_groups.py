from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import GroupConfig
from app.db.models import Group


def sync_groups(session: Session, groups: list[GroupConfig]) -> None:
    """Upsert groups.yaml entries into the groups table (source of truth is the YAML file)."""
    for g in groups:
        row = session.get(Group, g.id)
        if row is None:
            session.add(Group(id=g.id, name=g.name, enabled=g.enabled, notes=g.notes))
        else:
            row.name = g.name
            row.enabled = g.enabled
            row.notes = g.notes
