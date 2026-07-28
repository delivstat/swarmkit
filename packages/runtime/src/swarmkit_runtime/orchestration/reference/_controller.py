"""The generic reference saga controller (design/details/bundled-pipeline-orchestrator.md).

Domain-neutral: it sequences a resolved StageGraph's stages as a saga over the runtime's
``RunStage`` drive seam, persisting `SagaState` after each transition. It threads only artifact
**references** (keyed by ``(correlation_id, stage)``) — never content; the runtime writes/resolves
content via the ArtifactStore. It reacts to three inbound event forms:

- ``{"kind":"start","graph":…}`` — begin a saga on an explicitly named graph (``swarmkit pipeline
  emit`` / a direct ``/pipelines/signal``).
- ``{"kind":"gate",…}`` — a funnel's human decision (resume or terminate a parked saga).
- a **named pipeline event** — a bare event name (e.g. ``requirement.created`` from a webhook
  trigger's ``emit``) or ``{"kind":"event","name":…}``. A fresh correlation whose event matches a
  graph's entry ``when:`` starts that graph. This is what lets webhook/CI/Jira triggers drive the
  bundled orchestrator without hand-writing a ``start`` payload.

Everything durable lives in the store, so a restart resumes mid-saga.

Imported only by the ``swarmkit orchestrator`` command — never the runtime core or serve.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from swarmkit_runtime.orchestration import RunStage, StageOutcome
from swarmkit_runtime.orchestration._saga import SagaState, SagaStore

logger = logging.getLogger("swarmkit.orchestration")


def _stage_id(stage: Mapping[str, Any], index: int) -> str:
    return str(stage.get("id") or stage.get("agent") or stage.get("topology") or f"stage-{index}")


def _entry_events(graph: Mapping[str, Any]) -> set[str]:
    """The events that start a graph: the ``when:`` of its first stage (its external entry)."""
    stages = list(graph.get("stages") or [])
    if not stages:
        return set()
    return {str(e) for e in (stages[0].get("when") or [])}


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
        """React to one dequeued event: ``start`` / ``gate`` / a named pipeline event (see module
        docstring). Duplicate ``start`` is idempotent; an unroutable event is logged, not silently
        dropped (the queue's claim already dedups delivery)."""
        try:
            data = json.loads(event)
        except (json.JSONDecodeError, TypeError):
            data = None

        if isinstance(data, dict) and data.get("kind") == "start":
            await self._start(correlation_id, data)
            return
        if isinstance(data, dict) and data.get("kind") == "gate":
            await self._resolve_gate(correlation_id, data)
            return

        # Otherwise: a named pipeline event. It is either a structured `{"kind":"event","name":…}`
        # or a bare event-name string (a webhook trigger's `emit`). Route it against the graphs.
        name = str(data.get("name", "")) if isinstance(data, dict) else str(event)
        payload = data if isinstance(data, dict) else {}
        await self._on_named_event(correlation_id, name, payload)

    async def _on_named_event(
        self, correlation_id: str, name: str, data: Mapping[str, Any]
    ) -> None:
        """Route a named pipeline event. A fresh correlation whose event names a graph's entry
        ``when:`` starts that graph; anything else is a logged no-op (never a silent drop)."""
        if not name:
            return
        if self._store.get(correlation_id) is not None:
            # An external named event for an already-tracked saga. The bundled controller advances
            # sequentially / via gates, so it does not re-route mid-run — log and drop.
            logger.info(
                "pipeline event %r for existing saga %r ignored (bundled controller advances "
                "sequentially; use a gate event to resume)",
                name,
                correlation_id,
            )
            return
        matches = [gid for gid, g in self._graphs.items() if name in _entry_events(g)]
        if len(matches) != 1:
            logger.warning(
                "pipeline event %r (correlation %r) matched %d entry graphs %r — no run started "
                "(expected exactly one; check the StageGraph first stage's `when:`)",
                name,
                correlation_id,
                len(matches),
                matches,
            )
            return
        await self._start(
            correlation_id,
            {"graph": matches[0], "tag": name, "input": str(data.get("input", ""))},
        )

    async def _start(self, correlation_id: str, data: dict[str, Any]) -> None:
        if self._store.get(correlation_id) is not None:
            return  # idempotent: a repeated start is a no-op
        graph_id = str(data.get("graph", ""))
        saga = self._store.create(
            correlation_id,
            graph_id=graph_id,
            tag=str(data.get("tag", "")),
            input=str(data.get("input", "")),
        )
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
