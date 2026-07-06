"""Shared SQLite connection helpers for the runtime's stores.

Four stores — persistence (``persistence/_sqlite.py``), audit (``audit/_sqlite.py``), the prompt
ring buffer (``telemetry/_ring_buffer.py``), and notifications (``notifications/_store.py``) — each
hand-rolled the same connection setup: ensure the parent dir, open the DB, enable WAL (+ usually
``synchronous=NORMAL``), then create the table(s) + indexes. This centralises that lifecycle so a
persistence-policy change (WAL tuning, a busy_timeout, an eventual pool) is one edit.

Two access patterns coexist and both are supported here: a **persistent** single connection
(audit / telemetry / notifications hold ``self._conn`` for the object's life, using
``check_same_thread=False``) and a **per-call** connection (the persistence store opens a fresh
connection under a lock per operation). ``wal_connection`` serves both via its keyword options.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def wal_connection(
    db_path: str | Path,
    *,
    check_same_thread: bool = True,
    timeout: float = 5.0,
    synchronous: str | None = None,
    foreign_keys: bool = False,
    row_factory: Any = None,
) -> sqlite3.Connection:
    """Open a SQLite connection in WAL mode with the store's common PRAGMAs.

    Ensures the parent directory exists. WAL lets concurrent readers proceed while a writer holds
    the lock. *synchronous* (e.g. ``"NORMAL"``) trades a little durability for speed; *foreign_keys*
    turns on FK enforcement; *row_factory* (e.g. ``sqlite3.Row``) gives dict-like rows;
    *check_same_thread=False* is required for a connection shared across threads.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=timeout, check_same_thread=check_same_thread)
    conn.execute("PRAGMA journal_mode=WAL")
    if synchronous is not None:
        conn.execute(f"PRAGMA synchronous={synchronous}")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys=ON")
    if row_factory is not None:
        conn.row_factory = row_factory
    return conn


def bootstrap(conn: sqlite3.Connection, schema: str, indexes: Sequence[str] = ()) -> None:
    """Create the store's table(s) from *schema* (+ any *indexes*) if absent, and commit.

    ``executescript`` handles both a single ``CREATE TABLE`` and a multi-statement schema.
    """
    conn.executescript(schema)
    for idx in indexes:
        conn.execute(idx)
    conn.commit()
