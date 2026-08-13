"""A parked run resumes over HTTP, and a re-run records what it supersedes.

Two gaps that between them made a deferred run a dead end for anything but the local CLI.

**Resume.** Serve could park a run on a human gate (1.182.0) and had no way to continue it:
`swarmkit run --resume` needs the workspace on the same machine, so an application that started a
run over HTTP and saw it defer was stuck. The state is checkpointed under `thread_id == job.id`, so
resuming needs nothing but the id — what was missing was the door.

It goes through `execute_job` with a `resume` flag rather than a second implementation, because a
resumed run can park *again*, and the semaphore slot, timeout, usage recording and deferral branch
all have to behave identically the second time.

**The chain.** A rejected artifact is redone by running again, which writes a NEW job.
`correlation_id` cannot express that: it already means "same ticket" and holds different units of
work as well as retries. `parent_job_id` says "this replaces that" — what makes cost across
retries answerable, and what lets a reader walk from an artifact back through the attempts.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest
from swarmkit_runtime.persistence._store import JobRow, Store, make_engine
from swarmkit_runtime.server._jobs import Job, JobStore


def _store(tmp_path: Path) -> Store:
    return Store(make_engine(f"sqlite:///{tmp_path / 's.sqlite'}"))


def _job(store: Store, job_id: str) -> JobRow:
    """`get_job` is Optional; every use here has just written the row."""
    row = store.get_job(job_id)
    assert row is not None
    return row


# ---- the chain ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rerun_records_what_it_supersedes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job("job-1", "wms-design", "draft it")
    store.create_job("job-2", "wms-design", "draft it", parent_job_id="job-1")

    assert _job(store, "job-2").parent_job_id == "job-1"
    assert _job(store, "job-1").parent_job_id is None


@pytest.mark.asyncio
async def test_a_chain_is_walkable(tmp_path: Path) -> None:
    """Three attempts at one ticket: the chain answers "what did this artifact really cost"."""
    store = _store(tmp_path)
    store.create_job("job-1", "t", "i", "WMS-35", "cli")
    store.create_job("job-2", "t", "i", "WMS-35", "cli", parent_job_id="job-1")
    store.create_job("job-3", "t", "i", "WMS-35", "cli", parent_job_id="job-2")

    by_id = {j.id: j for j in store.list_jobs()}
    chain, cursor = [], "job-3"
    while cursor:
        chain.append(cursor)
        cursor = by_id[cursor].parent_job_id or ""

    assert chain == ["job-3", "job-2", "job-1"]


@pytest.mark.asyncio
async def test_the_chain_is_a_different_fact_from_the_correlation(tmp_path: Path) -> None:
    """A correlation groups a ticket's runs — including different units of work, not just retries.
    "Same ticket" and "replaces that attempt" cannot be the same field."""
    store = _store(tmp_path)
    store.create_job("triage", "wms-triage", "i", "WMS-35", "pipeline")
    store.create_job("design", "wms-design", "i", "WMS-35", "pipeline")
    store.create_job("design-2", "wms-design", "i", "WMS-35", "pipeline", parent_job_id="design")

    same_ticket = {j.id for j in store.list_jobs(correlation_id="WMS-35")}
    assert same_ticket == {"triage", "design", "design-2"}
    assert _job(store, "triage").parent_job_id is None


@pytest.mark.asyncio
async def test_the_column_is_added_to_an_existing_table(tmp_path: Path) -> None:
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

    assert "parent_job_id" in {c["name"] for c in inspect(engine).get_columns("jobs")}
    store.create_job("job-2", "t", "i", parent_job_id="job-1")
    assert _job(store, "job-2").parent_job_id == "job-1"


@pytest.mark.asyncio
async def test_the_chain_reaches_a_reader(tmp_path: Path) -> None:
    """A column nobody can read is the shape of defect this codebase keeps finding."""
    from swarmkit_runtime.server._routes_jobs import _resolve_job  # noqa: PLC0415

    store = _store(tmp_path)
    store.create_job("job-2", "t", "i", parent_job_id="job-1")

    view = _resolve_job(
        Job(id="job-2", topology="t", status="completed", input="i"), _job(store, "job-2")
    )

    assert view.parent_job_id == "job-1"


@pytest.mark.asyncio
async def test_every_durable_field_still_reaches_the_view(tmp_path: Path) -> None:
    """The guard from bug 28, re-run against the column added here — which is exactly the case it
    was written to catch."""
    from swarmkit_runtime.server._routes_jobs import _resolve_job  # noqa: PLC0415

    row = JobRow(id="job-2", topology="t", status="completed", input="i", parent_job_id="job-1")
    view = _resolve_job(Job(id="job-2", topology="t", status="completed", input="i"), row)

    for f in dataclass_fields(JobRow):
        assert getattr(view, f.name, None) == getattr(row, f.name), f"{f.name} unreachable"


# ---- resume -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_deferred_job_is_rehydrated_for_resume() -> None:
    """A run parked before a restart has no live object, and `execute_job` mutates one. Without
    rehydration a deferred row would be permanently stuck — visible and unresumable."""
    row = JobRow(
        id="job-1",
        topology="wms-design",
        status="deferred",
        input="draft it",
        error="awaiting review: gate 'job-1:designer'",
    )
    store = JobStore()

    job = await store.adopt(row)

    assert job.id == "job-1"
    assert job.status == "deferred"
    assert job.input == "draft it"
    assert await store.get("job-1") is job, "and it is tracked, so the next GET finds it"


def test_resume_shares_the_run_machinery() -> None:
    """One `execute_job` with a flag, not a second implementation: a resumed run can park AGAIN,
    and the semaphore slot, timeout, usage recording and deferral branch must behave the same the
    second time. A parallel implementation would drift exactly there."""
    src = (Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_jobs.py").read_text()

    assert "resume: bool = False" in src
    assert "rt.resume(job.topology, job.id, max_steps=max_steps)" in src


def test_only_a_deferred_job_resumes() -> None:
    """A completed run has nothing to continue and a running one is already going — starting a
    second execution against one checkpoint would interleave two runs on it."""
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_routes_jobs.py"
    ).read_text()
    handler = src[src.index('@app.post("/jobs/{job_id}/resume")') :][:1800]

    assert 'found.status != "deferred"' in handler
    assert "status_code=409" in handler


def test_the_resume_route_is_registered() -> None:
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_routes_jobs.py"
    ).read_text()

    assert '@app.post("/jobs/{job_id}/resume")' in src


def test_resume_is_a_sibling_of_start() -> None:
    """Both go through JobService, so neither can acquire a concern the other lacks."""
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_services.py"
    ).read_text()

    assert "async def resume(" in src
    assert "resume=True" in src


@pytest.mark.asyncio
async def test_adopt_survives_a_row_missing_optional_fields() -> None:
    """A row from an older schema must not break resumption."""

    class _Sparse:
        id = "job-1"
        topology = "t"
        status = "deferred"
        input = "i"

    job = await JobStore().adopt(_Sparse())

    assert job.id == "job-1"
    assert job.events == []
