"""The generic reference saga controller (design/details/bundled-pipeline-orchestrator.md).

Domain-neutral: it sequences a resolved StageGraph's stages as a saga over the runtime's
``RunStage`` drive seam, persisting `SagaState` after each transition. It threads only artifact
**references** (keyed by ``(correlation_id, stage)``) — never content; the runtime writes/resolves
content via the ArtifactStore. Two inbound event kinds: ``start`` (begin a saga on a graph) and
``gate`` (a funnel's human decision — resume or terminate). Everything durable lives in the store,
so a restart resumes mid-saga.

Imported only by the ``swarmkit orchestrator`` command — never the runtime core or serve.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from swarmkit_runtime.orchestration import RunStage, StageOutcome
from swarmkit_runtime.orchestration._saga import SagaState, SagaStore


def _stage_id(stage: Mapping[str, Any], index: int) -> str:
    return str(stage.get("id") or stage.get("agent") or stage.get("topology") or f"stage-{index}")


class ReferenceController:
    """Drive sagas over a StageGraph registry + the ``RunStage`` seam."""

    def __init__(
        self,
        *,
        run_stage: RunStage,
        store: SagaStore,
        graphs: Mapping[
            str, Mapping[str, Any]
        ],  # graph_id -> the graph spec (stages under "stages")
    ) -> None:
        self._run = run_stage
        self._store = store
        self._graphs = graphs

    # ── inbound events (from the claimed queue) ─────────────────────────────────────────────────
    async def handle_event(self, correlation_id: str, event: str) -> None:
        """React to one dequeued event. ``start`` begins a saga; ``gate`` resolves the parked gate.
        Unknown/duplicate events are no-ops (the queue's claim already dedups delivery)."""
        try:
            data = json.loads(event)
        except (json.JSONDecodeError, TypeError):
            return
        kind = data.get("kind")
        if kind == "start":
            await self._start(correlation_id, data)
        elif kind == "gate":
            await self._resolve_gate(correlation_id, data)

    async def _start(self, correlation_id: str, data: dict[str, Any]) -> None:
        if self._store.get(correlation_id) is not None:
            return  # idempotent: a repeated start is a no-op
        graph_id = str(data.get("graph", ""))
        saga = self._store.create(correlation_id, graph_id=graph_id, tag=str(data.get("tag", "")))
        saga.add("new", detail=graph_id)
        self._store.save(saga)
        await self._drive(saga)

    async def _resolve_gate(self, correlation_id: str, data: dict[str, Any]) -> None:
        saga = self._store.get(correlation_id)
        if saga is None or saga.status != "parked":
            return
        stage = saga.pending_gate_stage
        approved = bool(data.get("approved", False))
        saga.pending_gate_stage = None
        if not approved:
            saga.status = "rejected"
            saga.add("rejected", stage_id=stage, detail=str(data.get("detail", "")))
            self._store.save(saga)
            return
        # approved: the parked stage passed; continue the saga.
        if stage is not None and stage not in saga.passed_stages:
            saga.passed_stages.append(stage)
        saga.status = "active"
        saga.add("resumed", stage_id=stage)
        self._store.save(saga)
        await self._drive(saga)

    # ── the drive loop ──────────────────────────────────────────────────────────────────────────
    async def _drive(self, saga: SagaState) -> None:
        stages = list(self._graphs.get(saga.graph_id, {}).get("stages") or [])
        while True:
            index = len(saga.passed_stages)
            if index >= len(stages):
                saga.status = "completed"
                saga.current_stage = None
                saga.add("completed")
                self._store.save(saga)
                return
            stage = stages[index]
            sid = _stage_id(stage, index)
            saga.current_stage = sid
            saga.attempts[sid] = saga.attempts.get(sid, 0) + 1
            saga.add("started", stage_id=sid)
            self._store.save(saga)

            outcome = await self._run(saga.correlation_id, dict(stage))

            if outcome.status == "completed":
                saga.passed_stages.append(sid)
                if outcome.artifact:  # a reference to the produced artifact, not its content
                    saga.artifacts[sid] = outcome.artifact
                saga.current_stage = None
                saga.add("completed", stage_id=sid, detail=outcome.detail)
                self._store.save(saga)
                continue  # advance to the next stage
            if outcome.status == "parked":
                saga.status = "parked"
                saga.pending_gate_stage = sid
                if outcome.artifact:
                    saga.artifacts[sid] = outcome.artifact
                saga.add("parked", stage_id=sid, detail=outcome.detail)
                self._store.save(saga)
                return  # wait for a `gate` event
            # rejected | denied | failed — terminal for this saga (surfaced to the human).
            saga.status = "failed" if outcome.status in ("failed", "denied") else "rejected"
            saga.current_stage = None
            saga.add(saga.status, stage_id=sid, detail=outcome.detail)
            self._store.save(saga)
            return


__all__ = ["ReferenceController", "StageOutcome"]
