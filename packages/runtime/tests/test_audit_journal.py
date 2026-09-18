"""The audit is a write-through journal (design/details/audit-event-journal.md).

Each recorded event reaches the durable store the moment it is recorded, not in one batch at the
run boundary — so a crashed run still leaves its trail. These tests exercise the seam directly
(`JournalingGovernance` + the runtime's `_journal_write`), without a subprocess: the end-to-end
kill -9 proof is `test_kill9_recovery.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime._run_scope import (
    reset_current_run_id,
    reset_current_topology,
    set_current_run_id,
    set_current_topology,
)
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.audit import SqlAuditProvider
from swarmkit_runtime.audit._journal import JournalingGovernance
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.persistence._store import make_engine

_WS = """\
apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: journal, name: Journal}
governance: {provider: mock}
memory: {enabled: false}
"""


def _runtime(tmp_path: Path) -> WorkspaceRuntime:
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "skills").mkdir()
    return WorkspaceRuntime.from_workspace_path(tmp_path)


def _event(event_type: str, agent_id: str) -> AuditEvent:
    return AuditEvent(event_type=event_type, agent_id=agent_id, timestamp=datetime.now(tz=UTC))


async def _all(provider: Any) -> list[tuple[str, str]]:
    return [(e.event_type, e.agent_id) async for e in provider.query(limit=1000)]


@pytest.mark.asyncio
async def test_an_event_is_in_the_store_before_the_run_ends(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    provider = rt._audit_provider
    run_token = set_current_run_id("run-1")
    topo_token = set_current_topology("t")
    try:
        # The node records; nothing calls _end_run. The store must already have it.
        await rt._governance.record_event(_event("agent.started", "worker"))
        assert ("agent.started", "worker") in await _all(provider)
    finally:
        reset_current_topology(topo_token)
        reset_current_run_id(run_token)


@pytest.mark.asyncio
async def test_the_journal_stamps_run_and_topology_at_write_time(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    run_token = set_current_run_id("run-9")
    topo_token = set_current_topology("my-topology")
    try:
        await rt._governance.record_event(_event("skill.executed", "worker"))
    finally:
        reset_current_topology(topo_token)
        reset_current_run_id(run_token)
    rows = [e async for e in rt._audit_provider.query(limit=10)]
    assert len(rows) == 1
    assert rows[0].run_id == "run-9"
    assert rows[0].topology_id == "my-topology"


@pytest.mark.asyncio
async def test_the_end_of_run_batch_does_not_duplicate_a_journaled_event(tmp_path: Path) -> None:
    """The store dedups on event_id, so re-persisting the same events at run end is a no-op."""
    rt = _runtime(tmp_path)
    run_token = set_current_run_id("run-2")
    topo_token = set_current_topology("t")
    try:
        event = _event("skill.executed", "worker")
        await rt._governance.record_event(event)  # journaled once, write-through
        # The batch rebuilds AuditEvents from the provider's log, reusing the same event_id.
        events = rt._governance.events  # type: ignore[attr-defined]  # delegated to the base
        from swarmkit_runtime._workspace_runtime import _extract_events  # noqa: PLC0415

        extracted = _extract_events(rt._governance, run_id="run-2")
        assert extracted and extracted[0].event_id == str(event.event_id)
        await rt._persist_events_to_audit(extracted, "t", "run-2", {})
    finally:
        reset_current_topology(topo_token)
        reset_current_run_id(run_token)
    rows = await _all(rt._audit_provider)
    assert rows.count(("skill.executed", "worker")) == 1, "journaled once, not doubled by the batch"
    assert events  # sanity: the in-memory list is still there for the RunResult


@pytest.mark.asyncio
async def test_a_failing_journal_write_is_swallowed_and_the_base_still_records(
    tmp_path: Path,
) -> None:
    """A store that will not take the write must not take the run down with it — the end-of-run
    batch is the retry. The base provider keeps the in-memory copy regardless."""
    base = MockGovernanceProvider()

    async def _boom(_event: AuditEvent) -> None:
        raise RuntimeError("store is down")

    gov = JournalingGovernance(base, write=_boom)
    await gov.record_event(_event("agent.started", "worker"))  # must not raise
    assert [e.event_type for e in base.events] == ["agent.started"]


@pytest.mark.asyncio
async def test_the_wrapper_delegates_everything_else(tmp_path: Path) -> None:
    base = MockGovernanceProvider()
    recorded: list[AuditEvent] = []

    async def _capture(event: AuditEvent) -> None:
        recorded.append(event)

    gov = JournalingGovernance(base, write=_capture)
    # A non-record_event method resolves through to the base.
    score = await gov.get_trust_score(agent_id="worker")
    assert score.score >= 0.0
    # `.events` and `._base` are visible for _extract_events.
    assert gov.events == base.events
    assert gov._base is base


# ---- backend-appropriate write path (Postgres off-loop, SQLite on-loop) -------------------------


@pytest.mark.asyncio
async def test_sqlite_audit_write_stays_on_the_loop(tmp_path: Path, monkeypatch: Any) -> None:
    """SQLite is a single writer: writing audit off-loop (in a thread) while the loop writes the job
    store to the same file yields `database is locked`. So the SQLite path must NOT use to_thread —
    it stays inline. (Postgres does use a thread; a DB round-trip releases the GIL there.)"""
    provider = SqlAuditProvider(make_engine(f"sqlite:///{tmp_path / 'a.sqlite'}"))
    assert provider._engine.dialect.name == "sqlite"

    called = {"to_thread": False}
    real = asyncio.to_thread

    async def _spy(fn: Any, *a: Any, **k: Any) -> Any:
        called["to_thread"] = True
        return await real(fn, *a, **k)

    monkeypatch.setattr("swarmkit_runtime.audit._store.asyncio.to_thread", _spy)
    await provider.record(_event("agent.started", "worker"))
    assert called["to_thread"] is False, "the SQLite audit write must stay on the loop"
    assert ("agent.started", "worker") in await _all(provider)
