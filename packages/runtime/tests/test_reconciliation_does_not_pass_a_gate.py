"""Reconciliation never advances a stage whose result a human was supposed to approve.

The bug-20 fix (1.168.0) absorbs a stage that completed while the orchestrator was down. It handled
the stage and not its gate: `passed_stages.append(sid)` then `_drive`, with `pending_gate_stage`
never set — so a stage declaring `gate:` was marked passed and the next stage started unreviewed::

    saga 'WMS-24' was waiting on stage 'triage', which had already completed — absorbing it.

    t+  0s ('active', 'triage', '[]',         None)
    t+ 20s ('active', 'design', '["triage"]', None)      <- straight to design, no gate

An orchestrator restart therefore converted a gated pipeline into an ungated one, for the stage that
happened to be in flight. Any restart triggers it: a deploy, an upgrade, an OOM, a reboot.

It is also the quiet direction of failure. Bug 20's stranding was a run that visibly never
progressed; this looks like a run that went fine, and "who approved this?" answers "nobody, a
restart did".

**Why refusing, rather than parking.** Parking correctly means opening the gate — fanning the
funnel's approve policy into role-tasks on the review queue — and that needs the workspace funnels,
the queue and the artifact, all of which live serve-side. The controller can only ever synthesise
`completed` from a job row. Setting `pending_gate_stage` without opening the funnel would park the
saga on a gate with no review-queue entry: a stall nobody can release, traded for a skipped
approval.

So a gated stage keeps bug 20's stranding, loudly and on the saga's own timeline, and a human
releases it. The work is not lost — it is in `jobs`, done and paid for. Automating past a human
decision is not a recovery.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.orchestration import SagaState, StageOutcome
from swarmkit_runtime.orchestration.reference._controller import ReferenceController

GATED: dict[str, Any] = {
    "stages": [
        {"id": "triage", "topology": "t", "gate": "triage-review"},
        {"id": "design", "topology": "d"},
    ]
}
UNGATED: dict[str, Any] = {
    "stages": [
        {"id": "triage", "topology": "t"},
        {"id": "design", "topology": "d"},
    ]
}


class _Store:
    def __init__(self, saga: SagaState) -> None:
        self.saga = saga

    def get(self, _cid: str) -> SagaState | None:
        return self.saga

    def save(self, saga: SagaState) -> None:
        self.saga = saga


def _stranded() -> SagaState:
    saga = SagaState(correlation_id="WMS-24", graph_id="g", input="the ticket")
    saga.current_stage = "triage"
    saga.attempts["triage"] = 1
    return saga


def _completed(*_a: Any) -> StageOutcome:
    return StageOutcome(status="completed", artifact="WMS-24/triage/output", detail="")


def _controller(store: _Store, graph: dict[str, Any], ran: list[str]) -> ReferenceController:
    async def _run(_cid: str, stage: dict[str, Any]) -> StageOutcome:
        ran.append(str(stage["id"]))
        return StageOutcome(status="completed", artifact="ref", detail="")

    return ReferenceController(
        run_stage=_run,
        store=store,  # type: ignore[arg-type]
        graphs={"g": graph},
        stage_result=_completed,
    )


# ---- a gated stage is not absorbed ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_gated_stage_is_not_marked_passed() -> None:
    """The reported reproduction: `triage` declares a gate and landed in `passed_stages`."""
    store = _Store(_stranded())

    await _controller(store, GATED, []).handle_event("WMS-24", "ticket.created")

    assert store.saga.passed_stages == []


@pytest.mark.asyncio
async def test_the_next_stage_does_not_run() -> None:
    """The consequence that matters — `design` ran on an unreviewed triage."""
    ran: list[str] = []

    await _controller(_Store(_stranded()), GATED, ran).handle_event("WMS-24", "ticket.created")

    assert ran == [], "nothing downstream may run before the approval happens"


@pytest.mark.asyncio
async def test_the_saga_is_not_left_looking_finished() -> None:
    """It stays exactly where it was: waiting, for a human."""
    store = _Store(_stranded())

    await _controller(store, GATED, []).handle_event("WMS-24", "ticket.created")

    assert store.saga.status == "active"
    assert store.saga.current_stage == "triage"


@pytest.mark.asyncio
async def test_the_refusal_is_on_the_timeline() -> None:
    """A run that is not moving has to say why. `pipeline status` reads the timeline; without an
    entry an operator sees only silence, which is what bug 20 felt like."""
    store = _Store(_stranded())

    await _controller(store, GATED, []).handle_event("WMS-24", "ticket.created")

    blocked = [e for e in store.saga.timeline if e.kind == "blocked"]
    assert len(blocked) == 1
    assert blocked[0].stage_id == "triage"
    assert "gate" in blocked[0].detail


@pytest.mark.asyncio
async def test_the_timeline_entry_is_written_once() -> None:
    """The lease keeps redelivering this event, and a timeline that scrolls is one nobody reads."""
    store = _Store(_stranded())
    controller = _controller(store, GATED, [])

    for _ in range(4):
        await controller.handle_event("WMS-24", "ticket.created")

    assert len([e for e in store.saga.timeline if e.kind == "blocked"]) == 1


@pytest.mark.asyncio
async def test_the_legacy_funnel_spelling_gates_too() -> None:
    """`funnel:` is the older key for the same thing; a stage using it is not absorbed either."""
    graph = {"stages": [{"id": "triage", "topology": "t", "funnel": "triage-review"}]}
    store = _Store(_stranded())

    await _controller(store, graph, []).handle_event("WMS-24", "ticket.created")

    assert store.saga.passed_stages == []


@pytest.mark.asyncio
async def test_an_unreadable_stage_spec_is_treated_as_gated() -> None:
    """Deciding on missing information, in the direction that skips reviews, is the bug itself. An
    unknown stage is not assumed ungated."""
    store = _Store(_stranded())

    await _controller(store, {"stages": [{"id": "something-else"}]}, []).handle_event(
        "WMS-24", "ticket.created"
    )

    assert store.saga.passed_stages == []


# ---- and bug 20's recovery still works where there is no gate ------------------------------------


@pytest.mark.asyncio
async def test_an_ungated_stage_is_still_absorbed() -> None:
    """The fix is scoped to gates: the stranding bug 20 reported stays fixed everywhere else."""
    store = _Store(_stranded())
    ran: list[str] = []

    await _controller(store, UNGATED, ran).handle_event("WMS-24", "ticket.created")

    assert store.saga.passed_stages == ["triage", "design"]
    assert ran == ["design"], "it picks up at the stage AFTER the one that finished"
