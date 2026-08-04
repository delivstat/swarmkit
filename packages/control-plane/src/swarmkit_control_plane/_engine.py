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


def create_all_idempotent(metadata: Any, engine: Engine) -> None:
    """``metadata.create_all`` that survives another process doing it at the same moment.

    ``create_all`` is check-then-create: two processes starting together both see a table absent
    and both issue ``CREATE``; one gets ``already exists`` and dies at startup. The panel runs
    alongside its own replicas and shares one database across four stores, so concurrent start is
    the normal case, not an edge one.

    A losing racer is not an error — the table it wanted exists — but this VERIFIES rather than
    swallowing: a blind ``except`` would hide a genuinely half-built schema until some later query
    failed somewhere unrelated. Anything that is not a duplicate-table error propagates untouched.

    Deliberately duplicated from `swarmkit_runtime.persistence._store`: the panel is a standalone
    app and never imports the runtime package (see docs/notes, control-plane-standalone). A
    contract test keeps the two honest.
    """
    from sqlalchemy import inspect  # noqa: PLC0415
    from sqlalchemy.exc import DatabaseError  # noqa: PLC0415

    last: Exception | None = None
    for _attempt in range(3):
        try:
            metadata.create_all(engine)
        except DatabaseError as exc:
            if "already exists" not in str(exc).lower():
                raise
            last = exc
        else:
            return
        existing = set(inspect(engine).get_table_names())
        if not {t.name for t in metadata.sorted_tables} - existing:
            return
    if last is not None:
        raise last


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
