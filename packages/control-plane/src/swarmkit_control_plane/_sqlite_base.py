"""Shared sqlite store base — one definition of the connection lifecycle.

The registry / aggregation / artifacts / proposals stores each reimplemented an identical
``__init__`` + ``_connect`` (WAL + busy_timeout) + ``threading.Lock`` + schema bootstrap.
This base holds that once, behind a single seam, so a persistence-policy change (WAL tuning,
a connection pool, the eventual Postgres swap) is one edit, and every store gets a migration
hook (only the registry had one before).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class SqliteStore:
    """Base for the panel's sqlite-backed stores. Subclasses set ``_SCHEMA`` (the
    CREATE-TABLE-IF-NOT-EXISTS script) and may override ``_migrate`` to add columns to a
    pre-existing DB."""

    _SCHEMA: str = ""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(self._SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the initial schema (pre-existing DBs). No-op by default."""

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL lets connector pushes and operator reads proceed concurrently without blocking;
        # busy_timeout retries under contention instead of raising "database is locked".
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @property
    def db_path(self) -> Path:
        """Backing sqlite path — the four stores share one file."""
        return self._db_path
