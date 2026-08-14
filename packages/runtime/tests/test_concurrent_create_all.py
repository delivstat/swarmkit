"""Two processes can build the same store at once without one of them dying at startup.

Found while investigating two CI failures — `table conversations already exists` and
`table fleet_enrollment_tokens already exists` — that both passed on re-run. They were not just a
test artifact.

``metadata.create_all`` is check-then-create: it lists existing tables, then issues ``CREATE`` for
the rest. Two processes starting together both see a table absent and both issue ``CREATE``; one
gets ``already exists`` and dies. Every comment in this codebase calls create_all "idempotent",
which is true within one process and false across two — and two is the normal case: `swarmkit serve`
and `swarmkit orchestrator` share a store, as do replicas of either, and the control-plane panel
shares one database across four stores.

Reproduced before the fix at **12/12 trials** with six processes against one SQLite file: 2 of 6
processes failed to start on the first trial alone.

The fix verifies rather than swallowing. A losing racer is not an error — the table it wanted
exists — but "already exists" is only benign if the tables really are there afterwards. A blind
`except` would hide a genuinely half-built schema until some later query failed somewhere
unrelated, which is the silent-degradation trade this codebase keeps paying for.
"""

from __future__ import annotations

import ast
import multiprocessing as mp
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Column, MetaData, Table, Text, inspect
from sqlalchemy.exc import DatabaseError
from swarmkit_runtime.persistence._store import create_all_idempotent, make_engine


def _build_saga_store(db: str, q: Any, barrier: Any) -> None:
    """Runs in a child process: exactly what `swarmkit orchestrator` does at startup.

    The barrier is what makes this deterministic. Process startup costs dominate otherwise, so the
    children reach ``create_all`` far enough apart to miss each other and the test passes with the
    bug present — which is exactly how this shipped: it only ever surfaced as an occasional CI
    flake. Releasing them together reproduces it every run.
    """
    try:
        from swarmkit_runtime.audit import SqlAuditProvider  # noqa: PLC0415

        engine = make_engine(f"sqlite:///{db}")
        barrier.wait(timeout=60)
        SqlAuditProvider(engine)
        q.put("ok")
    except Exception as exc:  # the child reports, the parent asserts
        q.put(f"{type(exc).__name__}: {exc}")


@pytest.mark.parametrize("workers", [6])
def test_concurrent_processes_all_start(tmp_path: Path, workers: int) -> None:
    """The bug as an operator meets it: bring up serve and the orchestrator together, one crashes.

    Real processes, not threads — the GIL would mask exactly the interleaving that fails.
    """
    db = str(tmp_path / "s.sqlite")
    # Initialise the FILE (and its WAL mode) in the parent, leaving the tables absent. Six children
    # switching a fresh database into WAL at the same instant is its own lock contention — real,
    # but a different problem from the one under test, and on a loaded runner it surfaced as
    # `database is locked` rather than the duplicate-table race. The race itself is untouched:
    # every child still finds the tables missing and still issues CREATE.
    _init = make_engine(f"sqlite:///{db}")
    with _init.connect():
        pass
    _init.dispose()

    ctx = mp.get_context("spawn")
    q: Any = ctx.Queue()
    barrier: Any = ctx.Barrier(workers)
    procs = [ctx.Process(target=_build_saga_store, args=(db, q, barrier)) for _ in range(workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)

    results = [q.get() for _ in procs]
    failures = [r for r in results if r != "ok"]
    assert not failures, f"{len(failures)}/{workers} processes failed to start: {failures[0]}"


# ---- the helper's contract -----------------------------------------------------------------------


def _metadata(name: str = "widgets") -> MetaData:
    md = MetaData()
    Table(name, md, Column("id", Text, primary_key=True))
    return md


def test_it_is_a_no_op_when_the_table_already_exists(tmp_path: Path) -> None:
    """The benign case: someone else won the race a moment ago."""
    db = tmp_path / "s.sqlite"
    engine = make_engine(f"sqlite:///{db}")
    try:
        create_all_idempotent(_metadata(), engine)
        create_all_idempotent(_metadata(), engine)  # must not raise
        assert "widgets" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_it_creates_what_is_missing(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite"
    engine = make_engine(f"sqlite:///{db}")
    try:
        create_all_idempotent(_metadata(), engine)
        assert "widgets" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_a_pre_existing_table_created_by_someone_else_is_accepted(tmp_path: Path) -> None:
    """The exact race outcome: the table exists, but not because we made it."""
    db = tmp_path / "s.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE widgets (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    engine = make_engine(f"sqlite:///{db}")
    try:
        create_all_idempotent(_metadata(), engine)
    finally:
        engine.dispose()


def test_an_unrelated_database_error_is_not_swallowed(tmp_path: Path) -> None:
    """The property that keeps this from becoming the next silent-degradation bug. Only
    duplicate-table errors are treated as benign; everything else propagates untouched."""

    class _Exploding:
        sorted_tables: list[Any] = []  # noqa: RUF012

        def create_all(self, _engine: Any) -> None:
            raise DatabaseError("stmt", {}, Exception("disk I/O error"))

    engine = make_engine(f"sqlite:///{tmp_path / 's.sqlite'}")
    try:
        with pytest.raises(DatabaseError, match="disk I/O error"):
            create_all_idempotent(_Exploding(), engine)
    finally:
        engine.dispose()


def test_a_persistently_missing_table_still_raises(tmp_path: Path) -> None:
    """`already exists` is only benign if the tables really are there. If they are not, the error
    was not the race we thought, and hiding it would defer the failure to an unrelated query."""

    class _LiesAboutExisting:
        """Claims a duplicate while never creating anything — a half-built schema."""

        sorted_tables = (Table("ghost", MetaData(), Column("id", Text, primary_key=True)),)

        def create_all(self, _engine: Any) -> None:
            raise DatabaseError("stmt", {}, Exception("table ghost already exists"))

    engine = make_engine(f"sqlite:///{tmp_path / 's.sqlite'}")
    try:
        with pytest.raises(DatabaseError, match="already exists"):
            create_all_idempotent(_LiesAboutExisting(), engine)
    finally:
        engine.dispose()


# ---- the boundary --------------------------------------------------------------------------------


def _raw_create_all_calls(root: Path, *, helper_module: str) -> list[str]:
    """Real ``x.create_all(...)`` calls, found by AST rather than by scanning text.

    Prose mentions it too — several module docstrings explain why it is called — and a text scan
    reports those as violations, which would make this guard cry wolf until someone deleted it.
    """
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == helper_module:  # the helper's own single call
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_all"
            ):
                found.append(f"{path.relative_to(root)}:{node.lineno}")
    return found


def test_no_store_calls_create_all_directly() -> None:
    """Seven call sites had the same latent race. A new store must not reintroduce it — and this is
    cheaper to enforce here than to rediscover from a flaky CI run months later."""
    root = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime"
    offenders = _raw_create_all_calls(root, helper_module="_store.py")
    assert not offenders, f"call create_all_idempotent instead: {offenders}"


def test_the_control_plane_keeps_its_own_copy() -> None:
    """The panel is standalone and never imports the runtime package, so the helper is duplicated
    by design. This is the contract test that keeps the two honest — the established pattern for
    that boundary."""
    cp = Path(__file__).resolve().parents[3] / "packages/control-plane/src/swarmkit_control_plane"
    if not cp.exists():  # pragma: no cover - runtime-only checkout
        pytest.skip("control-plane package not present")

    engine_src = (cp / "_engine.py").read_text()
    assert "def create_all_idempotent" in engine_src, "the panel needs its own copy"
    # The docstring NAMES the runtime module it mirrors; what must not exist is an import of it.
    imports = [
        line
        for line in engine_src.splitlines()
        if line.startswith(("import ", "from ")) and "swarmkit_runtime" in line
    ]
    assert not imports, f"the panel must not import the runtime to get it: {imports}"

    offenders = _raw_create_all_calls(cp, helper_module="_engine.py")
    assert not offenders, f"call create_all_idempotent instead: {offenders}"
