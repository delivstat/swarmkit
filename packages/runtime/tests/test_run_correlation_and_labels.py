"""A one-shot run can be correlated and labelled, and both reach the record.

`swarmkit run` had no way to set a correlation id, so `jobs.correlation_id` was NULL for every CLI
run. That breaks the trace chain at its first link — `jobs → audit_events.run_id →
pipeline_artifacts.ref` has nothing to hang off — and any cost rollup by correlation silently
under-counts rather than erroring.

Labels are the other half, and deliberately opaque: a caller groups runs by whatever it is
modelling — a map, a requirement, a tenant — and the runtime never learns what any of those are. It
carries the caller's model rather than imposing one, which is the difference between this and
teaching SwarmKit what a "map" is.

Both columns are additive and nullable, applied through the same small migration facility the store
already uses, because `create_all` does not alter an existing table and an upgraded deployment would
otherwise fail on its next insert with "no such column".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime._run_scope import (
    current_labels,
    reset_current_labels,
    set_current_labels,
)
from swarmkit_runtime.audit import SqlAuditProvider
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.persistence._store import Store, make_engine


def _store(tmp_path: Path) -> Store:
    return Store(make_engine(f"sqlite:///{tmp_path / 'store.sqlite'}"))


# ---- the correlation id ------------------------------------------------------------------------


def test_a_job_records_its_correlation_id(tmp_path: Path) -> None:
    """The first link of the chain, NULL on every CLI run until now."""
    store = _store(tmp_path)
    store.create_job("job-1", "wms-design", "input", "WMS-30", "cli")

    assert store.list_jobs()[0].correlation_id == "WMS-30"


def test_jobs_can_be_listed_by_correlation(tmp_path: Path) -> None:
    """What the chain is for: every run belonging to one ticket, in one query."""
    store = _store(tmp_path)
    store.create_job("job-1", "t", "a", "WMS-30", "cli")
    store.create_job("job-2", "t", "b", "WMS-31", "cli")
    store.create_job("job-3", "t", "c", "WMS-30", "cli")

    assert {j.id for j in store.list_jobs(correlation_id="WMS-30")} == {"job-1", "job-3"}


def test_an_uncorrelated_run_is_still_allowed(tmp_path: Path) -> None:
    """Correlation is opt-in — an ad-hoc run should not have to invent one."""
    store = _store(tmp_path)
    store.create_job("job-1", "t", "a")

    assert store.list_jobs()[0].correlation_id is None


# ---- labels ------------------------------------------------------------------------------------


def test_a_job_records_its_labels(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job(
        "job-1", "t", "a", "WMS-30", "cli", labels={"map": "WMS-30", "kind": "research"}
    )

    assert store.list_jobs()[0].labels == {"map": "WMS-30", "kind": "research"}


def test_labels_round_trip_as_json(tmp_path: Path) -> None:
    """Stored as JSON in one column, so a caller's keys need no schema change to add."""
    store = _store(tmp_path)
    store.create_job("job-1", "t", "a", labels={"tenant": "acme"})

    with store.engine.connect() as conn:
        from sqlalchemy import text  # noqa: PLC0415

        raw = conn.execute(text("select labels from jobs")).scalar_one()

    assert json.loads(raw) == {"tenant": "acme"}


def test_no_labels_is_an_empty_dict_not_none(tmp_path: Path) -> None:
    """A reader should never have to `or {}` — absent means empty here."""
    store = _store(tmp_path)
    store.create_job("job-1", "t", "a")

    assert store.list_jobs()[0].labels == {}


# ---- labels reach the audit log ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_audit_event_carries_the_runs_labels(tmp_path: Path) -> None:
    """The half that makes labels worth having: "what did this map cost" is an audit query."""
    provider = SqlAuditProvider(make_engine(f"sqlite:///{tmp_path / 'audit.sqlite'}"))
    token = set_current_labels({"map": "WMS-30"})
    try:
        await provider.record(
            AuditEvent(
                event_type="skill.executed",
                agent_id="researcher",
                timestamp=datetime.now(tz=UTC),
                run_id="run-1",
            )
        )
    finally:
        reset_current_labels(token)

    events = [e async for e in provider.query(run_id="run-1")]
    assert events and events[0].labels == {"map": "WMS-30"}


@pytest.mark.asyncio
async def test_labels_survive_the_audit_round_trip_unchanged(tmp_path: Path) -> None:
    provider = SqlAuditProvider(make_engine(f"sqlite:///{tmp_path / 'audit.sqlite'}"))
    labels = {"map": "WMS-30", "kind": "research", "tenant": "acme"}
    await provider.record(
        AuditEvent(
            event_type="x",
            agent_id="a",
            timestamp=datetime.now(tz=UTC),
            run_id="run-1",
            labels=labels,
        )
    )

    events = [e async for e in provider.query(run_id="run-1")]
    assert events[0].labels == labels


@pytest.mark.asyncio
async def test_an_unlabelled_event_has_an_empty_dict(tmp_path: Path) -> None:
    provider = SqlAuditProvider(make_engine(f"sqlite:///{tmp_path / 'audit.sqlite'}"))
    await provider.record(
        AuditEvent(event_type="x", agent_id="a", timestamp=datetime.now(tz=UTC), run_id="r")
    )

    events = [e async for e in provider.query(run_id="r")]
    assert events[0].labels == {}


# ---- the scope -----------------------------------------------------------------------------------


def test_labels_default_is_not_shared_between_tasks() -> None:
    """A mutable ContextVar default is shared by every task that never sets one — so mutating what
    one run reads would change what every other run reads. The default is None and copied out."""
    first = current_labels()
    first["poisoned"] = "yes"

    assert current_labels() == {}


def test_setting_labels_copies_the_caller_s_dict() -> None:
    """A caller mutating its own dict after the run started must not retroactively relabel it."""
    caller: dict[str, str] = {"map": "WMS-30"}
    token = set_current_labels(caller)
    try:
        caller["map"] = "WMS-99"
        assert current_labels() == {"map": "WMS-30"}
    finally:
        reset_current_labels(token)


# ---- the CLI parser --------------------------------------------------------------------------


def test_label_parsing_accepts_repeated_pairs() -> None:
    from swarmkit_runtime.cli._cmd_run import _parse_labels  # noqa: PLC0415

    assert _parse_labels(["map=WMS-30", "kind=research"]) == {"map": "WMS-30", "kind": "research"}


def test_label_parsing_allows_an_empty_value() -> None:
    """`--label draft=` is a legitimate marker; only a missing `=` is malformed."""
    from swarmkit_runtime.cli._cmd_run import _parse_labels  # noqa: PLC0415

    assert _parse_labels(["draft="]) == {"draft": ""}


@pytest.mark.parametrize("bad", ["nokey", "=novalue", " =x"])
def test_a_malformed_label_fails_the_run(bad: str) -> None:
    """Not dropped. A label exists to make a run findable later, and one silently discarded is a run
    quietly missing from whatever query the caller is about to trust."""
    import typer  # noqa: PLC0415
    from swarmkit_runtime.cli._cmd_run import _parse_labels  # noqa: PLC0415

    with pytest.raises(typer.Exit):
        _parse_labels([bad])


def test_no_labels_parses_to_empty() -> None:
    from swarmkit_runtime.cli._cmd_run import _parse_labels  # noqa: PLC0415

    assert _parse_labels(None) == {}


# ---- the migration -------------------------------------------------------------------------------


def test_labels_is_added_to_an_existing_jobs_table(tmp_path: Path) -> None:
    """`create_all` does not alter an existing table, so without the migration an upgraded
    deployment fails on its next insert with "no such column"."""
    from sqlalchemy import inspect, text  # noqa: PLC0415

    engine = make_engine(f"sqlite:///{tmp_path / 'old.sqlite'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, topology TEXT NOT NULL, "
                "status TEXT NOT NULL, input TEXT NOT NULL, created_at TEXT NOT NULL, "
                "events TEXT, version TEXT, output TEXT, error TEXT, completed_at TEXT, "
                "usage_input_tokens INTEGER, usage_output_tokens INTEGER, usage_cost_usd FLOAT)"
            )
        )

    store = Store(engine)

    assert "labels" in {c["name"] for c in inspect(engine).get_columns("jobs")}
    store.create_job("job-1", "t", "a", labels={"map": "WMS-30"})
    assert store.list_jobs()[0].labels == {"map": "WMS-30"}


def test_labels_is_added_to_an_existing_audit_table(tmp_path: Path) -> None:
    """The audit store had no migration facility at all before this column needed one."""
    from sqlalchemy import inspect, text  # noqa: PLC0415

    engine = make_engine(f"sqlite:///{tmp_path / 'oldaudit.sqlite'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE audit_events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, "
                "agent_id TEXT NOT NULL, timestamp TEXT NOT NULL, run_id TEXT, payload TEXT)"
            )
        )

    SqlAuditProvider(engine)

    assert "labels" in {c["name"] for c in inspect(engine).get_columns("audit_events")}


def test_the_migration_is_idempotent(tmp_path: Path) -> None:
    """Every construction runs it; a second one must not fail on an already-added column."""
    engine = make_engine(f"sqlite:///{tmp_path / 'twice.sqlite'}")
    Store(engine)
    Store(engine)

    audit: Any = make_engine(f"sqlite:///{tmp_path / 'twice-audit.sqlite'}")
    SqlAuditProvider(audit)
    SqlAuditProvider(audit)
