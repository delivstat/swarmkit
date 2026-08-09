"""A saga blocked by reconciliation can actually be released by the operator it points at.

Bug 24's fix (1.171.0) stopped reconciliation absorbing a stage whose gate a human was supposed to
approve. That was right, and it left the run in a state with **no working release path**:

* the saga is `active` with a `blocked` timeline entry and `pending_gate_stage` unset;
* `_resolve_gate` dropped anything not `parked`;
* `swarmkit pipeline advance` enqueued the event and printed success regardless.

So the refusal log named `swarmkit pipeline advance` as the remedy, the operator ran it, the CLI
said it had advanced, and the controller dropped the event. Reports success, changes nothing — the
same shape as the bugs this run of work has been fixing, this time introduced by one of the fixes.

The distinction bug 24 turns on is kept intact. Reconciliation still must not advance past a human
gate on its own. Releasing is not reconciliation: it is the human, arriving late, doing the
approving — so the absorb happens under an explicit operator act and is recorded as one.
"""

from __future__ import annotations

import json
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


class _Store:
    def __init__(self, saga: SagaState) -> None:
        self.saga = saga

    def get(self, _cid: str) -> SagaState | None:
        return self.saga

    def save(self, saga: SagaState) -> None:
        self.saga = saga


def _completed(*_a: Any) -> StageOutcome:
    return StageOutcome(status="completed", artifact="WMS-27/triage/output", detail="")


def _controller(store: _Store, ran: list[str]) -> ReferenceController:
    async def _run(_cid: str, stage: dict[str, Any]) -> StageOutcome:
        ran.append(str(stage["id"]))
        return StageOutcome(status="completed", artifact="ref", detail="")

    return ReferenceController(
        run_stage=_run,
        store=store,  # type: ignore[arg-type]
        graphs={"g": GATED},
        stage_result=_completed,
    )


async def _blocked(store: _Store, ran: list[str]) -> ReferenceController:
    """Drive a saga into the reported state: stranded on a gated stage that already finished."""
    controller = _controller(store, ran)
    await controller.handle_event("WMS-27", "ticket.created")
    assert store.saga.status == "active"
    assert any(e.kind == "blocked" for e in store.saga.timeline), "the fixture must be blocked"
    return controller


def _gate(*, approved: bool) -> str:
    """The event `swarmkit pipeline advance` / `skip` enqueues, verbatim."""
    return json.dumps({"kind": "gate", "approved": approved, "stage": "triage"})


def _stranded() -> SagaState:
    saga = SagaState(correlation_id="WMS-27", graph_id="g", input="the ticket")
    saga.current_stage = "triage"
    saga.attempts["triage"] = 1
    return saga


# ---- the release works --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_operator_can_release_a_blocked_saga() -> None:
    """The reported failure: this event was dropped, and the CLI said it had advanced."""
    store = _Store(_stranded())
    ran: list[str] = []
    controller = await _blocked(store, ran)

    await controller.handle_event("WMS-27", _gate(approved=True))

    assert "triage" in store.saga.passed_stages


@pytest.mark.asyncio
async def test_the_run_continues_after_the_release() -> None:
    """Releasing is only useful if the pipeline then moves."""
    store = _Store(_stranded())
    ran: list[str] = []
    controller = await _blocked(store, ran)

    await controller.handle_event("WMS-27", _gate(approved=True))

    assert ran == ["design"], "it must pick up at the stage AFTER the released one"


@pytest.mark.asyncio
async def test_the_released_stages_artifact_is_recorded() -> None:
    """The absorbed stage's output feeds the next one; releasing without it starves design."""
    store = _Store(_stranded())
    controller = await _blocked(store, [])

    await controller.handle_event("WMS-27", _gate(approved=True))

    assert store.saga.artifacts["triage"] == "WMS-27/triage/output"


@pytest.mark.asyncio
async def test_the_release_is_recorded_as_a_human_act() -> None:
    """ "Who approved this?" must answer with something other than silence — the whole reason bug 24
    refused to absorb automatically."""
    store = _Store(_stranded())
    controller = await _blocked(store, [])

    await controller.handle_event("WMS-27", _gate(approved=True))

    resumed = [e for e in store.saga.timeline if e.kind == "resumed"]
    assert resumed and "operator" in resumed[-1].detail


@pytest.mark.asyncio
async def test_an_operator_can_reject_a_blocked_saga() -> None:
    """The other half of a gate. A rejection must terminate the run, not strand it further."""
    store = _Store(_stranded())
    ran: list[str] = []
    controller = await _blocked(store, ran)

    await controller.handle_event("WMS-27", _gate(approved=False))

    assert store.saga.status == "rejected"
    assert ran == []


# ---- and the automatic path is still refused -----------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_alone_still_does_not_advance() -> None:
    """Bug 24's property, re-asserted here because this change is the one that could undo it: only
    an explicit gate event releases. Redelivery on its own must never be enough."""
    store = _Store(_stranded())
    ran: list[str] = []
    controller = await _blocked(store, ran)

    for _ in range(3):
        await controller.handle_event("WMS-27", "ticket.created")

    assert store.saga.passed_stages == []
    assert ran == []


@pytest.mark.asyncio
async def test_a_gate_event_for_a_running_saga_is_still_dropped() -> None:
    """The releasable state is specifically "blocked". An ordinary in-flight stage is not one, and
    releasing it would approve work that has not happened."""
    saga = _stranded()
    store = _Store(saga)
    controller = _controller(store, [])

    await controller.handle_event("WMS-27", _gate(approved=True))

    assert store.saga.passed_stages == []
    assert store.saga.status == "active"
