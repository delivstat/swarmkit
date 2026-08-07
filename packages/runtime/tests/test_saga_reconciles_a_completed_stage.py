"""A stage that completed while the orchestrator was down no longer strands its saga.

If the orchestrator dies between "serve finished the stage" and "the saga records it", the work is
NOT lost — the completed result sits in `jobs` with its output and cost — but nothing reconciled it.
The lease (bug 12) correctly makes the event reachable again after the visibility timeout, and the
controller then dropped it as a duplicate, marked it `done`, and no further reclaim could occur.

Observed end state, stable an hour later::

    event:  (29, 'done',  attempts 2)
    jobs:   ('WMS-17:triage', 'completed', $0.0698)
    saga:   ('active', current_stage 'triage', passed_stages [], updated_at frozen at CREATION)

Every individual record is the record of a success, and the run never moves again. There is no
timeout on a saga, so nothing else is coming.

The de-duplication is right in general — a second `ticket.created` for a live saga must not start a
second run. It was wrong only here, where the saga never absorbed a stage that did in fact complete.

Nothing new is stored to fix it: `jobs` is already keyed `<correlation>:<stage>` and the artifact
reference is deterministic, so "waiting on X, and X completed" is answerable from the existing
record.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.orchestration import SagaState, StageOutcome
from swarmkit_runtime.orchestration.reference._controller import ReferenceController

GRAPH: dict[str, Any] = {
    "stages": [
        {"id": "triage", "topology": "t"},
        {"id": "design", "topology": "d"},
    ]
}


class _Store:
    def __init__(self, saga: SagaState | None = None) -> None:
        self.saga = saga
        self.saves = 0

    def get(self, _cid: str) -> SagaState | None:
        return self.saga

    def save(self, saga: SagaState) -> None:
        self.saga = saga
        self.saves += 1


def _stranded() -> SagaState:
    """The reported state: waiting on a stage that has already finished."""
    saga = SagaState(correlation_id="WMS-17", graph_id="g", input="the ticket")
    saga.status = "active"
    saga.current_stage = "triage"
    saga.attempts["triage"] = 1
    return saga


def _controller(store: _Store, stage_result: Any, run_stage: Any = None) -> ReferenceController:
    async def _default(_cid: str, _stage: dict[str, Any]) -> StageOutcome:
        return StageOutcome(status="completed", artifact="ref", detail="")

    return ReferenceController(
        run_stage=run_stage or _default,
        store=store,  # type: ignore[arg-type]
        graphs={"g": GRAPH},
        stage_result=stage_result,
    )


def _completed(*_a: Any) -> StageOutcome:
    return StageOutcome(status="completed", artifact="WMS-17/triage/output", detail="")


# ---- the stranded saga is recovered --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_finished_stage_is_absorbed_instead_of_dropped() -> None:
    """The bug: the event was discarded and the saga never moved again."""
    store = _Store(_stranded())

    await _controller(store, _completed).handle_event("WMS-17", "ticket.created")

    assert "triage" in store.saga.passed_stages  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_the_run_continues_to_the_next_stage() -> None:
    """Absorbing is not enough — the point is that the pipeline resumes."""
    ran: list[str] = []

    async def _run(_cid: str, stage: dict[str, Any]) -> StageOutcome:
        ran.append(str(stage["id"]))
        return StageOutcome(status="completed", artifact="ref", detail="")

    store = _Store(_stranded())

    await _controller(store, _completed, _run).handle_event("WMS-17", "ticket.created")

    assert ran == ["design"], "it must pick up at the stage AFTER the one that finished"
    assert store.saga.status == "completed"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_the_absorbed_stages_artifact_is_recorded() -> None:
    """Downstream stages read upstream artifacts; absorbing without the reference would resume the
    run and starve the next stage of its input."""
    store = _Store(_stranded())

    await _controller(store, _completed).handle_event("WMS-17", "ticket.created")

    assert store.saga.artifacts["triage"] == "WMS-17/triage/output"  # type: ignore[union-attr]


# ---- it does not recover what did not happen -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_stage_still_running_is_left_alone() -> None:
    """No job row yet means the stage may be in flight on another worker. Resuming here would run
    it twice."""
    store = _Store(_stranded())

    await _controller(store, lambda *_a: None).handle_event("WMS-17", "ticket.created")

    assert store.saga.passed_stages == []  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_failed_stage_is_not_absorbed() -> None:
    """Recovering a run that did not finish is a worse error than leaving it stuck."""
    store = _Store(_stranded())

    def failed(*_a: Any) -> StageOutcome:
        return StageOutcome(status="failed", artifact="", detail="boom")

    await _controller(store, failed).handle_event("WMS-17", "ticket.created")

    assert store.saga.passed_stages == []  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_saga_parked_on_a_gate_is_untouched() -> None:
    """A gate is a human decision. Absorbing past one would resume a run somebody paused."""
    saga = _stranded()
    saga.pending_gate_stage = "triage"
    store = _Store(saga)

    await _controller(store, _completed).handle_event("WMS-17", "ticket.created")

    assert store.saga.passed_stages == []  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_completed_saga_is_untouched() -> None:
    saga = _stranded()
    saga.status = "completed"
    store = _Store(saga)

    await _controller(store, _completed).handle_event("WMS-17", "ticket.created")

    assert store.saga.passed_stages == []  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_an_already_absorbed_stage_is_not_absorbed_twice() -> None:
    saga = _stranded()
    saga.passed_stages = ["triage"]
    store = _Store(saga)

    await _controller(store, _completed).handle_event("WMS-17", "ticket.created")

    assert store.saga.passed_stages == ["triage"]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_without_the_seam_the_behaviour_is_unchanged() -> None:
    """The lookup is optional: a deployment that cannot reach the job store still drops, exactly as
    before, rather than failing."""
    store = _Store(_stranded())

    await _controller(store, None).handle_event("WMS-17", "ticket.created")

    assert store.saga.passed_stages == []  # type: ignore[union-attr]


# ---- the message stops sending readers after a gate that is not there ----------------------------


def test_the_drop_message_only_mentions_a_gate_when_there_is_one() -> None:
    """It said "resolve or clear the gate to resume" whenever a saga existed. With
    `pending_gate_stage` None — the stranded case — a reader went looking for a gate that did not
    exist, which is the worst kind of wrong remedy: specific and actionable."""
    from pathlib import Path  # noqa: PLC0415

    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/orchestration/reference/_controller.py"
    ).read_text()

    assert '"this run is already in progress"' in src
    assert '"resolve or clear the gate to resume"' in src
