"""The `swarmkit orchestrator` drive loop (design/details/bundled-pipeline-orchestrator.md,
slice 5): claim events from the durable queue, drive the saga via the ReferenceController, ack
after applying - durable and idempotent."""

from __future__ import annotations

import json
from typing import Any

import pytest
from swarmkit_runtime.cli._cmd_orchestrator import run_drive_loop
from swarmkit_runtime.orchestration import InMemorySagaStore, StageOutcome
from swarmkit_runtime.orchestration.reference import ReferenceController

pytestmark = pytest.mark.asyncio

_GRAPH: dict[str, Any] = {"stages": [{"id": "build"}, {"id": "review"}, {"id": "deploy"}]}


def _controller(store: InMemorySagaStore) -> ReferenceController:
    async def run_stage(_cid: str, stage: dict[str, Any]) -> StageOutcome:
        if stage.get("id") == "review":
            return StageOutcome(status="parked", artifact="ref://review")
        return StageOutcome(status="completed", artifact=f"ref://{stage.get('id')}")

    return ReferenceController(run_stage=run_stage, store=store, graphs={"g": _GRAPH})


async def test_drive_loop_processes_queue_until_drained() -> None:
    store = InMemorySagaStore()
    ctl = _controller(store)
    store.enqueue("c1", json.dumps({"kind": "start", "graph": "g", "tag": "req-1"}))

    handled = await run_drive_loop(ctl, store, once=True)
    assert handled == 1
    saga = store.get("c1")
    assert saga is not None and saga.status == "parked"  # drove to the gate

    # a gate-approval event drives it to completion on the next pass
    store.enqueue("c1", json.dumps({"kind": "gate", "approved": True}))
    handled2 = await run_drive_loop(ctl, store, once=True)
    assert handled2 == 1
    done = store.get("c1")
    assert done is not None and done.status == "completed"


async def test_drive_loop_acks_only_after_applying() -> None:
    store = InMemorySagaStore()
    ctl = _controller(store)
    eid = store.enqueue("c1", json.dumps({"kind": "start", "graph": "g"}))
    await run_drive_loop(ctl, store, once=True)
    # the event was claimed + acked (done), so a second pass has nothing to claim
    assert store.claim("w") is None
    assert eid == 1
