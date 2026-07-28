"""The bundled reference orchestrator: durable saga store + the generic controller
(design/details/bundled-pipeline-orchestrator.md).

The store persists/resumes saga state, dedups events, and atomically claims from the durable queue;
the controller drives a StageGraph over a fake RunStage seam — completing, parking at a gate then
resuming, and terminating on rejection — identically under the in-memory and SQL stores.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import create_engine
from swarmkit_runtime.orchestration import (
    InMemorySagaStore,
    SagaStore,
    SqlSagaStore,
    StageOutcome,
)
from swarmkit_runtime.orchestration.reference import ReferenceController

pytestmark = pytest.mark.asyncio

_GRAPH: dict[str, Any] = {"stages": [{"id": "build"}, {"id": "review"}, {"id": "deploy"}]}


def _sql() -> SqlSagaStore:
    return SqlSagaStore(create_engine("sqlite:///:memory:"))


def _start(graph: str = "g", tag: str = "") -> str:
    return json.dumps({"kind": "start", "graph": graph, "tag": tag})


def _gate(approved: bool) -> str:
    return json.dumps({"kind": "gate", "approved": approved})


# ── the durable store ─────────────────────────────────────────────────────────────────────────────
async def test_sql_store_persists_and_resumes() -> None:
    store = _sql()
    saga = store.create("c1", graph_id="g", tag="req-42")
    saga.passed_stages.append("build")
    saga.status = "parked"
    saga.pending_gate_stage = "review"
    saga.artifacts["build"] = "ref://build"
    store.save(saga)

    # a fresh store on the same engine sees the persisted state (restart survival)
    reopened = SqlSagaStore(store.engine).get("c1")
    assert reopened is not None
    assert reopened.status == "parked" and reopened.pending_gate_stage == "review"
    assert reopened.passed_stages == ["build"] and reopened.artifacts["build"] == "ref://build"
    assert reopened.tag == "req-42"


async def test_event_queue_claim_is_atomic_and_dedup() -> None:
    store = _sql()
    e1 = store.enqueue("c1", "a")
    store.enqueue("c1", "b")
    first = store.claim("w1")
    assert first is not None and first[0] == e1  # oldest first
    second = store.claim("w2")
    assert second is not None and second[2] == "b"
    assert store.claim("w3") is None  # both claimed, queue drained
    store.ack(e1)

    key = ("c1", "start", "src-1")
    assert store.seen(key) is False
    store.mark_seen(key)
    assert store.seen(key) is True


async def test_list_filters_by_status_and_query() -> None:
    store = _sql()
    a = store.create("run-a", graph_id="g", tag="site-42")
    a.status = "completed"
    store.save(a)
    store.create("run-b", graph_id="g", tag="site-17")  # active

    assert {s.correlation_id for s in store.list(status="active")} == {"run-b"}
    assert {s.correlation_id for s in store.list(status="completed")} == {"run-a"}
    assert {s.correlation_id for s in store.list(query="site-42")} == {"run-a"}


# ── the controller drive loop (both stores) ─────────────────────────────────────────────────────
def _controller(store: SagaStore) -> ReferenceController:
    async def run_stage(_cid: str, stage: dict[str, Any]) -> StageOutcome:
        # 'review' parks on its gate; everything else completes with an artifact ref.
        if stage.get("id") == "review":
            return StageOutcome(
                status="parked", artifact="ref://review", detail="awaiting approval"
            )
        return StageOutcome(status="completed", artifact=f"ref://{stage.get('id')}")

    return ReferenceController(run_stage=run_stage, store=store, graphs={"g": _GRAPH})


@pytest.mark.parametrize("store_factory", [InMemorySagaStore, _sql])
async def test_controller_drives_completes_after_gate(
    store_factory: Callable[[], SagaStore],
) -> None:
    store = store_factory()
    ctl = _controller(store)

    await ctl.handle_event("c1", _start("g", "req-42"))
    parked = store.get("c1")
    assert parked is not None
    assert parked.status == "parked" and parked.pending_gate_stage == "review"
    assert parked.passed_stages == ["build"] and parked.artifacts["review"] == "ref://review"

    await ctl.handle_event("c1", _gate(True))
    done = store.get("c1")
    assert done is not None
    assert done.status == "completed"
    assert done.passed_stages == ["build", "review", "deploy"]
    assert [t.kind for t in done.timeline][-1] == "completed"


async def test_start_event_input_is_persisted_on_the_saga() -> None:
    # The bug fix: `{"kind":"start", …, "input": …}` carries a payload that must survive onto the
    # saga (and through a restart) so the first stage can be seeded with it.
    store = _sql()
    ctl = _controller(store)
    await ctl.handle_event(
        "c1", json.dumps({"kind": "start", "graph": "g", "input": "BRD-42: split shipment"})
    )
    reopened = SqlSagaStore(store.engine).get("c1")
    assert reopened is not None and reopened.input == "BRD-42: split shipment"


async def test_controller_gate_rejection_terminates() -> None:
    store = _sql()
    ctl = _controller(store)
    await ctl.handle_event("c1", _start())
    await ctl.handle_event("c1", _gate(False))
    saga = store.get("c1")
    assert saga is not None and saga.status == "rejected"
    assert "deploy" not in saga.passed_stages  # never advanced past the rejected gate


async def test_controller_survives_restart_mid_saga() -> None:
    store = _sql()
    await _controller(store).handle_event("c1", _start("g", "req-42"))  # parks at review
    # a brand-new controller + store on the same engine resumes from the persisted parked state
    fresh = SqlSagaStore(store.engine)
    await _controller(fresh).handle_event("c1", _gate(True))
    resumed = fresh.get("c1")
    assert resumed is not None and resumed.status == "completed"


# ── named pipeline events (webhook triggers) ────────────────────────────────────────────────────
# A graph whose first stage declares an external entry event, plus a second ungated stage — so a
# named-event start drives to completion.
_ENTRY_GRAPH: dict[str, Any] = {
    "stages": [{"id": "intake", "when": ["requirement.created"]}, {"id": "design"}]
}


async def _always_complete(_cid: str, stage: dict[str, Any]) -> StageOutcome:
    return StageOutcome(status="completed", artifact=f"ref://{stage.get('id')}")


def _entry_controller(store: SagaStore, **graphs: dict[str, Any]) -> ReferenceController:
    return ReferenceController(
        run_stage=_always_complete, store=store, graphs=graphs or {"oms": _ENTRY_GRAPH}
    )


async def test_bare_named_event_starts_the_matching_graph() -> None:
    # The webhook-trigger bug: a bare `emit` name (not a {"kind":"start"} payload) must start the
    # graph whose entry `when:` names it.
    store = InMemorySagaStore()
    await _entry_controller(store).handle_event("c-hook", "requirement.created")
    saga = store.get("c-hook")
    assert saga is not None
    assert saga.graph_id == "oms" and saga.tag == "requirement.created"
    assert saga.status == "completed" and saga.passed_stages == ["intake", "design"]


async def test_structured_named_event_threads_input() -> None:
    store = InMemorySagaStore()
    await _entry_controller(store).handle_event(
        "c-hook2",
        json.dumps({"kind": "event", "name": "requirement.created", "input": "BRD-9"}),
    )
    saga = store.get("c-hook2")
    assert saga is not None and saga.input == "BRD-9" and saga.graph_id == "oms"


async def test_unknown_named_event_starts_nothing() -> None:
    store = InMemorySagaStore()
    await _entry_controller(store).handle_event("c-x", "not.an.entry.event")
    assert store.get("c-x") is None


async def test_ambiguous_named_event_starts_nothing() -> None:
    # Two graphs claim the same entry event → no run (ambiguous), rather than guessing.
    store = InMemorySagaStore()
    ctl = _entry_controller(store, a=_ENTRY_GRAPH, b=_ENTRY_GRAPH)
    await ctl.handle_event("c-amb", "requirement.created")
    assert store.get("c-amb") is None


async def test_named_event_for_existing_saga_is_noop() -> None:
    store = InMemorySagaStore()
    ctl = _entry_controller(store)
    await ctl.handle_event("c-dup", "requirement.created")
    graph_before = store.get("c-dup").graph_id  # type: ignore[union-attr]
    await ctl.handle_event("c-dup", "requirement.created")  # a repeat must not error or re-start
    assert store.get("c-dup").graph_id == graph_before  # type: ignore[union-attr]
