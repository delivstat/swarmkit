"""SQLAlchemy engine construction for the control-plane stores.

The panel stays **standalone** (design D1): this mirrors the runtime's
``persistence._store.make_engine`` rather than importing it, so the control-plane never depends on
``swarmkit-runtime``. One implementation drives both SQLite (default) and Postgres
(design/details/postgres-backend.md) — a store is built from a SQLAlchemy URL.

WAL + ``busy_timeout`` are set per-connection for the SQLite dialect only (via a ``connect``
event), preserving the prior concurrency behaviour (connector pushes and operator reads proceed
without blocking; contention retries instead of raising "database is locked").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event


def normalize_url(url: str) -> str:
    """Point bare ``postgres://`` / ``postgresql://`` URLs at the psycopg 3 driver.

    A ``DATABASE_URL`` is commonly ``postgresql://…``, which SQLAlchemy maps to psycopg2 (not a
    dependency here); rewrite it to ``postgresql+psycopg://…`` so it uses the installed psycopg 3.
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def sqlite_url(path: Path) -> str:
    """Build the SQLite file URL for a backing path."""
    return f"sqlite:///{path}"


def make_engine(url: str) -> Engine:
    """Create a SQLAlchemy engine, enabling WAL + busy_timeout for the SQLite dialect.

    For a SQLite *file* URL the parent directory is created if absent (matching the prior stores),
    so a fresh data dir works on first run.
    """
    engine = create_engine(normalize_url(url))
    if engine.dialect.name == "sqlite":
        db_file = engine.url.database
        if db_file and db_file != ":memory:":
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _rec: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=10000")
            cur.close()

    return engine


__all__ = ["make_engine", "normalize_url", "sqlite_url"]
