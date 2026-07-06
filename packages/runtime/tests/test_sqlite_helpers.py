"""Unit tests for the shared SQLite connection helpers (_sqlite.wal_connection / bootstrap)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from swarmkit_runtime._sqlite import bootstrap, wal_connection


def test_wal_connection_creates_parent_and_enables_wal(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "dir" / "store.sqlite"
    conn = wal_connection(db)
    try:
        assert db.parent.is_dir()  # parent created
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_wal_connection_optional_pragmas(tmp_path: Path) -> None:
    conn = wal_connection(
        tmp_path / "s.sqlite",
        synchronous="NORMAL",
        foreign_keys=True,
        row_factory=sqlite3.Row,
    )
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL == 1
        conn.execute("CREATE TABLE t (a, b)")
        conn.execute("INSERT INTO t VALUES (1, 2)")
        row = conn.execute("SELECT * FROM t").fetchone()
        assert row["a"] == 1 and row["b"] == 2  # row_factory gives dict-like access
    finally:
        conn.close()


def test_bootstrap_creates_table_and_indexes_idempotently(tmp_path: Path) -> None:
    schema = "CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, kind TEXT)"
    indexes = ["CREATE INDEX IF NOT EXISTS idx_kind ON items (kind)"]
    conn = wal_connection(tmp_path / "b.sqlite")
    try:
        bootstrap(conn, schema, indexes)
        bootstrap(conn, schema, indexes)  # idempotent — IF NOT EXISTS, no error
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        idxs = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "items" in tables and "idx_kind" in idxs
    finally:
        conn.close()


def test_bootstrap_multistatement_schema(tmp_path: Path) -> None:
    schema = """
    CREATE TABLE IF NOT EXISTS a (x);
    CREATE TABLE IF NOT EXISTS b (y);
    """
    conn = wal_connection(tmp_path / "m.sqlite")
    try:
        bootstrap(conn, schema)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"a", "b"} <= tables
    finally:
        conn.close()
