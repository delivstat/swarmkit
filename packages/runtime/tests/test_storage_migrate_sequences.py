"""`swarmkit storage migrate` must leave the destination WRITABLE, not merely populated.

Reported against 1.133.0. The migration copies rows into Postgres **with their original primary
keys** and never advances the owning sequences, so a sequence sits at 1 while `max(id)` is 14. The
next insert reuses an id that is already there:

    UniqueViolation: duplicate key value violates unique constraint "pipeline_events_pkey"
    DETAIL: Key (id)=(1) already exists.

`pipeline_events` is the blocking one — every `pipeline emit` writes there, so after a migration no
pipeline could be started at all.

Why it was easy to miss, and why these tests exist: the migration reported success and printed row
counts; every READ worked, so runs displayed correctly in the UI; and the failure surfaced later, on
an unrelated-looking write, naming a constraint rather than the migration. Meanwhile `swarmkit
serve` kept advising operators to run the very command that left the store unwritable.

A second defect found while reproducing: `psycopg` reports `rowcount == -1` for an executemany, so
a migration that copied all 14 rows announced "0 copied, 14 already present" — the exact output of a
re-run that did nothing.

The Postgres tests need a real server (sequences do not exist in SQLite); set
SWARMKIT_TEST_POSTGRES_URL to run them.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
import typer
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from swarmkit_runtime.cli import _cmd_storage
from swarmkit_runtime.cli._cmd_storage import _owned_sequences, _resync_sequences
from swarmkit_runtime.persistence._store import make_engine

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _pg_engine() -> Any:
    url = os.environ.get("SWARMKIT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set SWARMKIT_TEST_POSTGRES_URL to run the Postgres migrate tests")
    return make_engine(url)


@pytest.fixture
def table(request: pytest.FixtureRequest) -> Any:
    """A throwaway serial-PK table in its own schema, dropped afterwards.

    Per-test schemas are not only for isolation under xdist. They are how a shared Postgres actually
    looks — SwarmKit's tables are one schema among several — so running these tests this way is also
    the assertion that the re-sync stays inside its own schema and leaves the neighbours alone.
    """
    from sqlalchemy import event  # noqa: PLC0415

    engine = _pg_engine()
    schema = f"seqtest_{uuid.uuid4().hex[:8]}"
    name = "widgets"

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute(f"SET search_path TO {schema}")
        cur.close()

    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE {name} (id SERIAL PRIMARY KEY, label TEXT)"))

    def _drop() -> None:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        engine.dispose()

    request.addfinalizer(_drop)
    return engine, name


def _seed_with_explicit_ids(engine: Any, name: str, ids: list[int]) -> None:
    """Insert rows carrying their own ids — what the migration does, and the reason the sequence is
    left behind."""
    with engine.begin() as conn:
        for i in ids:
            conn.execute(
                text(f"INSERT INTO {name} (id, label) VALUES (:i, :l)"), {"i": i, "l": f"row{i}"}
            )


def _last_value(engine: Any, name: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT last_value FROM {name}_id_seq")).scalar() or 0)


# ---- the reported bug ---------------------------------------------------------------------------


def test_a_migrated_table_can_accept_the_next_insert(table: Any) -> None:
    """The bug as the user meets it: not "is the data there" but "can I start a pipeline"."""
    engine, name = table
    _seed_with_explicit_ids(engine, name, list(range(1, 15)))

    assert _last_value(engine, name) == 1, "precondition: the sequence is stale after a raw copy"

    _resync_sequences(engine)

    with engine.begin() as conn:
        new_id = conn.execute(
            text(f"INSERT INTO {name} (label) VALUES ('after-migration') RETURNING id")
        ).scalar()
    assert new_id == 15, f"next insert must continue past the copied rows, got id={new_id}"


def test_without_the_resync_the_insert_dies(table: Any) -> None:
    """Pins the failure so a revert is loud. This is the exact UniqueViolation from the report."""
    engine, name = table
    _seed_with_explicit_ids(engine, name, list(range(1, 15)))

    with pytest.raises(IntegrityError, match="duplicate key value"), engine.begin() as conn:
        conn.execute(text(f"INSERT INTO {name} (label) VALUES ('boom')"))


def test_every_owned_sequence_is_found_not_a_hard_coded_list(table: Any) -> None:
    """Enumerated from pg_depend, so a table added later is covered without anyone remembering to
    update the migration — the failure being avoided is silent."""
    engine, name = table
    found = {t for _seq, t, _col in _owned_sequences(engine)}
    assert name in found, f"{name} was created moments ago and must be discovered automatically"


def test_a_neighbouring_schema_is_left_alone(table: Any) -> None:
    """A Postgres instance is routinely shared. Migrating SwarmKit must not reach into another
    application's schema and move its sequences — a migration that fixes its own store by quietly
    editing somebody else's is a worse bug than the one it fixes."""
    engine, _name = table
    neighbour = f"other_{uuid.uuid4().hex[:8]}"
    try:
        with engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {neighbour}"))
            conn.execute(
                text(f"CREATE TABLE {neighbour}.invoices (id SERIAL PRIMARY KEY, label TEXT)")
            )
            # Rows with explicit ids, sequence deliberately left behind — exactly the state the
            # re-sync looks for. If it ranged across schemas it would "helpfully" fix this too.
            for i in range(1, 6):
                conn.execute(
                    text(f"INSERT INTO {neighbour}.invoices (id, label) VALUES (:i, 'x')"), {"i": i}
                )
            before = conn.execute(
                text(f"SELECT last_value FROM {neighbour}.invoices_id_seq")
            ).scalar()

        _resync_sequences(engine)

        with engine.connect() as conn:
            after = conn.execute(
                text(f"SELECT last_value FROM {neighbour}.invoices_id_seq")
            ).scalar()
        assert after == before, "the neighbouring schema's sequence must not be touched"
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {neighbour} CASCADE"))


def test_resync_is_idempotent_and_never_regresses_a_live_sequence(table: Any) -> None:
    """Re-running the migration is explicitly supported. It must not drag a sequence backwards
    behind rows written since — that would reintroduce the very collision it fixes."""
    engine, name = table
    _seed_with_explicit_ids(engine, name, list(range(1, 15)))
    _resync_sequences(engine)

    with engine.begin() as conn:
        live = conn.execute(
            text(f"INSERT INTO {name} (label) VALUES ('live') RETURNING id")
        ).scalar()

    _resync_sequences(engine)  # a second migrate run

    with engine.begin() as conn:
        nxt = conn.execute(
            text(f"INSERT INTO {name} (label) VALUES ('next') RETURNING id")
        ).scalar()
    assert nxt == live + 1, "a re-run must not hand out an id that already exists"


def test_an_empty_table_does_not_burn_id_one(table: Any) -> None:
    """`setval(seq, max(id), true)` on an empty table means COALESCE(max, 1) — which silently skips
    id 1. Smaller than the bug being fixed, but the same kind, so it is not worth trading."""
    engine, name = table
    _resync_sequences(engine)

    with engine.begin() as conn:
        first = conn.execute(
            text(f"INSERT INTO {name} (label) VALUES ('first') RETURNING id")
        ).scalar()
    assert first == 1, f"the first row of an empty table should be id 1, got {first}"


def test_a_still_stale_sequence_fails_the_migration_loudly(table: Any, monkeypatch: Any) -> None:
    """The invariant that makes the rest trustworthy: a migration that leaves the store unable to
    accept an insert must NOT report success. That is the whole shape of this bug class."""
    engine, name = table
    _seed_with_explicit_ids(engine, name, list(range(1, 15)))

    # Simulate a setval that silently fails to take (a permissions problem, a replica, a future
    # Postgres change) — the migration must notice rather than announce success.
    real_begin = engine.begin

    class _NoOpSetval:
        def __init__(self, conn: Any) -> None:
            self._conn = conn

        def execute(self, stmt: Any, params: Any = None) -> Any:
            if "setval" in str(stmt):
                return None
            return self._conn.execute(stmt, params) if params else self._conn.execute(stmt)

        def __getattr__(self, item: str) -> Any:
            return getattr(self._conn, item)

    class _Ctx:
        def __enter__(self) -> Any:
            self._cm = real_begin()
            return _NoOpSetval(self._cm.__enter__())

        def __exit__(self, *a: Any) -> Any:
            return self._cm.__exit__(*a)

    monkeypatch.setattr(engine, "begin", _Ctx)

    with pytest.raises(typer.BadParameter, match="still behind"):
        _cmd_storage._resync_sequences(engine)


# ---- the reporting defect found while reproducing ------------------------------------------------


def test_copied_count_is_not_taken_from_rowcount() -> None:
    """psycopg returns rowcount == -1 for an executemany, so the migration reported "0 copied, 14
    already present" after copying all 14 — indistinguishable from a no-op re-run. An operator
    reading that would reasonably conclude the migration had already been done."""
    src = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/cli/_cmd_storage.py"
    body = src.read_text()
    assert "int(result.rowcount)" not in body, "rowcount is unreliable for executemany here"
    assert "after) - int(before)" in body, "count either side of the insert instead"


def test_sqlite_destinations_are_left_alone(tmp_path: Path) -> None:
    """Sequences are a Postgres concept. A SQLite destination must be a no-op, not an error — this
    runs without a server, so it guards the common path in CI."""
    db = tmp_path / "s.sqlite"
    sqlite3.connect(str(db)).close()
    engine = make_engine(f"sqlite:///{db}")
    try:
        assert _resync_sequences(engine) == []
    finally:
        engine.dispose()
