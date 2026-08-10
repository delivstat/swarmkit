"""An audit event is recorded against the run that emitted it, and no other.

`_extract_events` drained the governance provider's ENTIRE accumulated log on every run, and
`_persist_events_to_audit` stamped all of it with the current run's `run_id` and `topology_id`.
Nothing ever cleared that log, so a second run in the same process re-persisted the first run's
events under its own id::

    run 1 (triage) drains: [('skill.executed', 'triage')]
    run 2 (design) drains: [('skill.executed', 'triage'), ('skill.executed', 'designer')]

Reported as "`audit_events.agent_id` is wrong": `run_id='WMS-24:design'` with
`topology_id='wms-design'` and `agent_id='triage'`, on a topology whose agent is `designer`. The
field is not wrong — the row does not belong to that run at all.

Three consequences, in rising order of how badly they mislead:

* every event is rewritten once per subsequent run, so the table grows quadratically;
* "what did this run cost", grouped by `run_id`, over-counts by everything that came before it;
* the obvious workaround — joining on `run_id`/`topology_id` instead of `agent_id` — is **also
  unsafe**, because `run_id` is precisely the field being mis-stamped.

**Why stamping at emission rather than slicing the log.** Taking the tail of the provider's list
(everything appended since the run began) is the smaller change and it is wrong under exactly the
case that matters: `swarmkit serve` runs jobs concurrently, and two runs interleave their appends,
so a tail slice mixes them. Attribution has to be recorded by the task that emits the event, which
is what the `ContextVar` in `swarmkit_runtime._run_scope` does — asyncio copies the context per
task, so concurrent runs cannot see each other's scope.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from swarmkit_runtime._run_scope import (
    current_run_id,
    reset_current_run_id,
    set_current_run_id,
)
from swarmkit_runtime._workspace_runtime import _extract_events
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.governance._mock import MockGovernanceProvider


def _event(agent_id: str) -> AuditEvent:
    return AuditEvent(
        event_type="skill.executed", agent_id=agent_id, timestamp=datetime.now(tz=UTC)
    )


async def _emit(gov: MockGovernanceProvider, run_id: str, agent_id: str) -> None:
    """Emit one event inside a run scope, the way a node does."""
    token = set_current_run_id(run_id)
    try:
        await gov.record_event(_event(agent_id))
    finally:
        reset_current_run_id(token)


# ---- the reported failure --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_later_run_does_not_re_persist_an_earlier_runs_events() -> None:
    """The reproduction, at the seam that produced it."""
    gov = MockGovernanceProvider(allow_all=True)
    await _emit(gov, "WMS-24:triage", "triage")
    await _emit(gov, "WMS-24:design", "designer")

    design = _extract_events(gov, run_id="WMS-24:design")

    assert [e.agent_id for e in design] == ["designer"]


@pytest.mark.asyncio
async def test_the_earlier_run_still_gets_its_own_events() -> None:
    """Filtering must not cost a run its own record — the first drain was the correct one."""
    gov = MockGovernanceProvider(allow_all=True)
    await _emit(gov, "WMS-24:triage", "triage")
    await _emit(gov, "WMS-24:design", "designer")

    assert [e.agent_id for e in _extract_events(gov, run_id="WMS-24:triage")] == ["triage"]


@pytest.mark.asyncio
async def test_events_are_not_duplicated_across_many_runs() -> None:
    """The quadratic growth. Ten runs used to persist 1+2+…+10 = 55 rows for 10 events."""
    gov = MockGovernanceProvider(allow_all=True)
    total = 0
    for i in range(10):
        await _emit(gov, f"run-{i}", f"agent-{i}")
        total += len(_extract_events(gov, run_id=f"run-{i}"))

    assert total == 10


# ---- the case that ruled out slicing the log ---------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_runs_do_not_take_each_others_events() -> None:
    """Two jobs in one serve process, interleaved.

    A tail slice of the provider's list cannot separate these — the appends interleave — which is
    why attribution is stamped by the emitting task instead.
    """
    gov = MockGovernanceProvider(allow_all=True)

    async def job(run_id: str, agent_id: str, delay: float) -> None:
        token = set_current_run_id(run_id)
        try:
            for _ in range(3):
                await asyncio.sleep(delay)
                await gov.record_event(_event(agent_id))
        finally:
            reset_current_run_id(token)

    await asyncio.gather(job("run-a", "alpha", 0.001), job("run-b", "beta", 0.002))

    assert {e.agent_id for e in _extract_events(gov, run_id="run-a")} == {"alpha"}
    assert {e.agent_id for e in _extract_events(gov, run_id="run-b")} == {"beta"}
    assert len(_extract_events(gov, run_id="run-a")) == 3


@pytest.mark.asyncio
async def test_a_child_task_inherits_its_parents_run() -> None:
    """Delegation and parallel fan-out create tasks; their events belong to the same run.

    asyncio copies the context at task creation, so this holds without threading an id through
    every call — but it is the property the whole approach rests on, so it is asserted.
    """
    gov = MockGovernanceProvider(allow_all=True)

    async def child() -> None:
        await gov.record_event(_event("worker"))

    token = set_current_run_id("run-parent")
    try:
        await asyncio.gather(asyncio.create_task(child()), asyncio.create_task(child()))
    finally:
        reset_current_run_id(token)

    assert len(_extract_events(gov, run_id="run-parent")) == 2


# ---- the stamping itself -----------------------------------------------------------------------


def test_an_event_is_stamped_at_construction() -> None:
    """Not at persist time: an emitter should not have to know or pass the run id."""
    token = set_current_run_id("run-x")
    try:
        assert _event("a").run_id == "run-x"
    finally:
        reset_current_run_id(token)


def test_an_event_outside_a_run_carries_no_run_id() -> None:
    """Startup and shutdown emit events too, and they belong to no run."""
    assert current_run_id() is None
    assert _event("a").run_id is None


@pytest.mark.asyncio
async def test_events_from_outside_a_run_are_not_claimed_by_one() -> None:
    """An unattributed event is not this run's to record — it must not be swept in."""
    gov = MockGovernanceProvider(allow_all=True)
    await gov.record_event(_event("startup"))
    await _emit(gov, "run-a", "alpha")

    assert [e.agent_id for e in _extract_events(gov, run_id="run-a")] == ["alpha"]


@pytest.mark.asyncio
async def test_an_explicit_run_id_is_not_overwritten() -> None:
    """A caller that sets `run_id` itself means it — the default only fills an unset field."""
    token = set_current_run_id("scope-run")
    try:
        event: Any = AuditEvent(
            event_type="x", agent_id="a", timestamp=datetime.now(tz=UTC), run_id="explicit"
        )
    finally:
        reset_current_run_id(token)

    assert event.run_id == "explicit"


@pytest.mark.asyncio
async def test_no_run_id_keeps_the_unfiltered_behaviour() -> None:
    """Callers with no run to scope to still get everything, unchanged."""
    gov = MockGovernanceProvider(allow_all=True)
    await _emit(gov, "run-a", "alpha")
    await _emit(gov, "run-b", "beta")

    assert len(_extract_events(gov)) == 2
