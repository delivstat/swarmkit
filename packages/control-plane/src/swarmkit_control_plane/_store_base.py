"""Shared store base — one definition of the engine lifecycle for the four panel stores.

The registry / aggregation / artifacts / proposals stores are built on one SQLAlchemy engine (see
postgres-backend.md): the same Core code path drives SQLite (default) and Postgres. A store is
constructed from a backing **path** (SQLite file, the back-compat form the tests use), a **URL**, or
a shared **Engine** (so the four stores in one panel share a single connection pool).

``metadata.create_all`` is idempotent and only creates absent tables, so existing SQLite databases
keep working; ``_migrate`` remains the hook for adding columns to a pre-existing table that
``create_all`` won't ALTER. A ``threading.Lock`` is retained so the in-process serialisation the
stores relied on (notably the registry's command-claim) is unchanged.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, Table
from sqlalchemy.dialects import postgresql as sa_pg
from sqlalchemy.dialects import sqlite as sa_sqlite
from sqlalchemy.sql import Insert

from swarmkit_control_plane._engine import make_engine, sqlite_url
from swarmkit_control_plane._tables import metadata


def upsert(
    engine: Engine,
    table: Table,
    values: Mapping[str, Any],
    *,
    index_elements: Sequence[str],
    set_: Mapping[str, Any] | None = None,
) -> Insert:
    """Dialect-aware ``INSERT ... ON CONFLICT`` (SQLite ``INSERT OR REPLACE/IGNORE`` equivalent).

    ``set_=None`` → ``DO NOTHING`` (matches ``INSERT OR IGNORE``); a mapping → ``DO UPDATE SET`` on
    the given columns (matches ``INSERT OR REPLACE`` when every non-key column is set). The two
    dialect ``insert()`` constructs are the only supported way to express upsert portably.
    """
    insert = sa_pg.insert if engine.dialect.name == "postgresql" else sa_sqlite.insert
    stmt = insert(table).values(**dict(values))
    if set_ is None:
        return stmt.on_conflict_do_nothing(index_elements=list(index_elements))
    return stmt.on_conflict_do_update(index_elements=list(index_elements), set_=dict(set_))


class Store:
    """Base for the panel's SQLAlchemy-backed stores over any supported dialect."""

    def __init__(self, backing: Path | str | Engine) -> None:
        if isinstance(backing, Engine):
            self._engine = backing
        elif isinstance(backing, Path):
            self._engine = make_engine(sqlite_url(backing))
        elif "://" in backing:
            self._engine = make_engine(backing)
        else:
            self._engine = make_engine(sqlite_url(Path(backing)))
        self._lock = threading.Lock()
        metadata.create_all(self._engine)
        with self._engine.begin() as conn:
            self._migrate(conn)

    def _migrate(self, conn: Any) -> None:
        """Add columns introduced after a table's initial schema (pre-existing DBs). No-op by
        default; ``create_all`` handles fresh databases in full."""

    @property
    def engine(self) -> Engine:
        """The backing engine — shared by the four stores in one panel."""
        return self._engine

    @property
    def db_path(self) -> Path | None:
        """Backing SQLite file path, or ``None`` under Postgres. Back-compat accessor."""
        if self._engine.dialect.name != "sqlite":
            return None
        db = self._engine.url.database
        return Path(db) if db and db != ":memory:" else None


__all__ = ["Store", "upsert"]
