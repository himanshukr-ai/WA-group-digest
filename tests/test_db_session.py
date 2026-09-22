from __future__ import annotations

from app.db.session import _normalize_database_url


def test_normalize_postgres_scheme():
    assert _normalize_database_url("postgres://u:p@host:5432/db") == "postgresql+psycopg://u:p@host:5432/db"


def test_normalize_postgresql_scheme():
    assert _normalize_database_url("postgresql://u:p@host:5432/db") == "postgresql+psycopg://u:p@host:5432/db"


def test_normalize_leaves_sqlite_and_already_qualified_urls_alone():
    assert _normalize_database_url("sqlite:///./digest.db") == "sqlite:///./digest.db"
    assert _normalize_database_url("postgresql+psycopg://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
