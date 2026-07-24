"""The pipeline controller — a self-contained saga-sequencing service.

Not a SwarmKit runtime feature and not an agent: the controller owns durable per-instance
saga state and drives bounded SwarmKit stage runs over the ``run_stage`` **seam** (design
"Where it lives" + "SwarmKit seams it depends on"). This is the Minder split
(``feedback_llm_language_code_doing``): the application owns logic + state; SwarmKit does bounded
determination + governance inside each stage run. The controller never embeds the runtime — in
the demo/tests the seam wraps ``StageRunner`` or a scripted stub; in production it is a
``swarmkit serve`` HTTP call.

See design/details/pipeline-controller.md for the full semantics this implements.
"""

from __future__ import annotations

from ._events import (
    CloseGateCallback,
    InboundEvent,
    RunStage,
    SourceStateProvider,
    StageRunOutcome,
    StageRunRequest,
    SurfaceCallback,
    SurfaceNotice,
)
from ._graph import Stage, StageGraph
from ._locks import LockManager
from ._saga import InMemorySagaStore, SagaState, SagaStore, TimelineEntry, now

# Retries of a *failed* stage run before it surfaces to a human (design "Failure vs wait").
DEFAULT_MAX_ATTEMPTS = 3


class PipelineController:
    """Sequences a pipeline instance across a stage-graph as a durable, event-driven saga."""

    def __init__(
        self,
        graph: StageGraph,
        run_stage: RunStage,
        *,
        store: SagaStore | None = None,
        external_events: tuple[str, ...] = (),
        source_state: SourceStateProvider | None = None,
        on_surface: SurfaceCallback | None = None,
        on_close_gate: CloseGateCallback | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self._graph = graph
        self._run = run_stage
        self._store: SagaStore = store or InMemorySagaStore()
        # Entry events that are *externally sourced* (a CI/Jira/SAST webhook) — the controller
        # never fabricates them from a prior stage's completion; they arrive as a webhook or are
        # recovered by reconciliation. Everything else is an internal signal the controller emits.
        self._external = set(external_events)
        self._source_state = source_state
        self._on_surface = on_surface
        self._on_close_gate = on_close_gate
        self._max_attempts = max_attempts
        self._locks = LockManager()
        self._seq = 0  # monotonic source id for controller-emitted signals
        self._log_seq = 0  # monotonic timeline sequence

    # ---- read-side accessors (correlation) -----------------------------------------------

    def saga(self, correlation_id: str) -> SagaState | None:
        return self._store.get(correlation_id)

    def all_sagas(self) -> list[SagaState]:
        return [s for rid in self._store.all_ids() if (s := self._store.get(rid)) is not None]

    def timeline(self, correlation_id: str) -> list[TimelineEntry]:
        """The correlated saga timeline for a correlation id (every run carries its id)."""
        saga = self._store.get(correlation_id)
        return list(saga.timeline) if saga is not None else []

    # ---- inbound events ------------------------------------------------------------------

    async def handle_event(self, event: InboundEvent) -> None:
        """React to one inbound event: dedupe, release due locks, then route to a stage."""
        saga = self._store.get(event.correlation_id)
        if saga is not None and self._store.seen(event.correlation_id, event.key()):
            self._log(saga, None, "event.duplicate", f"{event.event} (dup) ignored")
            return
        if saga is None:
            saga = self._store.create(event.correlation_id)
        self._store.mark_seen(event.correlation_id, event.key())

        if saga.status in ("cancelled", "done"):
            self._log(saga, None, "event.ignored", f"{event.event} on {saga.status} saga")
            return

        self._log(saga, None, "event.received", f"{event.event} (src={event.source_event_id})")
        # Release any locks whose `release_locks_on` signal this event is, before advancing —
        # a freed contract must be available to a queued instance.
        await self._release_for_event(saga, event.event)

        route = self._graph.route(event.event)
        if route is None:
            # A signal no stage consumes (e.g. the terminal stage's success) — nothing to do.
            return
        await self._start_stage(saga, route.stage, event.payload, is_loop=route.is_loop)

    async def resolve_gate(self, correlation_id: str, *, approved: bool, detail: str = "") -> None:
        """Learn that a gate resolved (the gate-resolution seam) and react.

        On approval the controller completes the parked stage and emits its ``success`` signal;
        on rejection the stage is a terminal outcome the controller surfaces (design
        "Failure vs wait" — a gate rejection is not a retryable failure).
        """
        saga = self._store.get(correlation_id)
        if saga is None or saga.pending_gate is None or saga.pending_gate_stage is None:
            return
        stage = self._graph.stage(saga.pending_gate_stage)
        gate = saga.pending_gate
        saga.pending_gate = None
        saga.pending_gate_stage = None
        self._close_gate(saga, stage.id, gate)
        if approved:
            self._log(saga, stage.id, "gate.approved", gate)
            await self._complete_stage(saga, stage)
        else:
            self._log(saga, stage.id, "gate.rejected", detail or gate)
            saga.current_stages.discard(stage.id)
            saga.status = "failed"
            await self._release_locks(saga, tuple(stage.locks))
            self._store.save(saga)
            self._surface(saga, stage.id, "gate rejected the artifact", detail)

    async def reconcile(self, correlation_id: str | None = None) -> None:
        """Pull (mock) source-system state and advance any saga past a dropped event.

        Events are the fast path; reconciliation is the safety net (design "Reconciliation").
        For each correlation id, every source-confirmed event that has not already been processed
        and that routes somewhere is delivered — so a saga advances even if the webhook that
        would have carried it went missing.
        """
        if self._source_state is None:
            return
        ids = [correlation_id] if correlation_id is not None else self._store.all_ids()
        for rid in ids:
            saga = self._store.get(rid)
            if saga is None or saga.status in ("cancelled", "done", "failed"):
                continue
            for name in sorted(self._source_state(rid)):
                event = InboundEvent(rid, name, source_event_id=f"reconcile:{name}")
                if self._store.seen(rid, event.key()) or self._graph.route(name) is None:
                    continue
                self._log(saga, None, "reconcile.deliver", f"source shows {name}; delivering")
                await self.handle_event(event)

    async def cancel(self, correlation_id: str, *, detail: str = "") -> None:
        """Withdraw a pipeline instance: release locks, close gate tasks, compensate in reverse.

        A saga must have an unwind path (design "Cancellation + compensation"): each
        already-passed stage's ``compensation`` topology runs in reverse order.
        """
        saga = self._store.get(correlation_id)
        if saga is None or saga.status in ("cancelled", "done"):
            return
        self._log(saga, None, "cancel.requested", detail)

        if saga.pending_gate is not None:
            self._close_gate(saga, saga.pending_gate_stage, saga.pending_gate)
            saga.pending_gate = None
            saga.pending_gate_stage = None

        await self._release_locks(saga, tuple(saga.held_locks))
        saga.lock_release_triggers.clear()

        for stage_id in reversed(saga.passed_stages):
            stage = self._graph.stage(stage_id)
            if stage.compensation is None:
                continue
            self._log(saga, stage_id, "compensation.run", f"topology={stage.compensation}")
            request = StageRunRequest(
                correlation_id=correlation_id,
                stage_id=stage_id,
                topology=stage.compensation,
                gate=None,
                payload=f"compensate {stage_id}",
                is_compensation=True,
            )
            try:
                outcome = await self._run(request)
            except Exception as exc:  # surface, never crash the unwind
                self._log(saga, stage_id, "compensation.failed", f"{type(exc).__name__}: {exc}")
                self._surface(saga, stage_id, "compensation failed", str(exc))
                continue
            if outcome.status == "failed":
                self._log(saga, stage_id, "compensation.failed", outcome.detail)
                self._surface(saga, stage_id, "compensation failed", outcome.detail)

        saga.current_stages.clear()
        saga.status = "cancelled"
        self._store.save(saga)

    # ---- stage lifecycle -----------------------------------------------------------------

    async def _start_stage(
        self, saga: SagaState, stage: Stage, payload: str, *, is_loop: bool
    ) -> None:
        if is_loop:
            # Defect-cycle re-entry: re-run even a passed stage (and reset its attempt count).
            if stage.id in saga.passed_stages:
                saga.passed_stages.remove(stage.id)
            saga.attempts.pop(stage.id, None)
            self._log(saga, stage.id, "loop.reentry", "defect cycle re-entered")
        elif stage.id in saga.passed_stages or stage.id in saga.current_stages:
            # Idempotent forward advance: the same logical event delivered twice (webhook +
            # reconciliation) must not re-run or double-advance a stage.
            self._log(saga, stage.id, "stage.idempotent-skip", "already active/passed")
            return

        if stage.locks:
            if not self._locks.try_acquire(saga.correlation_id, stage.locks):
                saga.status = "parked"
                saga.pending_lock_stage = stage.id
                saga.pending_lock_payload = payload
                holders = {lid: self._locks.holder(lid) for lid in stage.locks}
                self._log(
                    saga,
                    stage.id,
                    "stage.parked-lock",
                    f"awaiting locks {holders}",
                )
                self._store.save(saga)
                return
            saga.held_locks.update(stage.locks)
            if stage.release_locks_on is not None:
                saga.lock_release_triggers.setdefault(stage.release_locks_on, []).extend(
                    stage.locks
                )
            self._log(saga, stage.id, "lock.acquired", f"{sorted(stage.locks)}")

        saga.pending_lock_stage = None
        saga.pending_lock_payload = ""
        saga.status = "active"
        saga.current_stages.add(stage.id)
        self._store.save(saga)
        await self._kick(saga, stage, payload)

    async def _kick(self, saga: SagaState, stage: Stage, payload: str) -> None:
        """Kick the bounded stage run over the seam, retrying a *failure* idempotently."""
        attempt = saga.attempts.get(stage.id, 0)
        while True:
            attempt += 1
            saga.attempts[stage.id] = attempt
            request = StageRunRequest(
                correlation_id=saga.correlation_id,
                stage_id=stage.id,
                topology=stage.topology,
                gate=stage.gate,
                payload=payload,
                attempt=attempt,
            )
            self._log(saga, stage.id, "stage.run", f"kick {stage.topology} (attempt {attempt})")
            try:
                outcome = await self._run(request)
            except Exception as exc:  # a crashed run is a retryable failure
                outcome = StageRunOutcome(status="failed", detail=f"{type(exc).__name__}: {exc}")
            if outcome.status != "failed":
                break
            self._log(saga, stage.id, "stage.failed", f"attempt {attempt}: {outcome.detail}")
            if attempt >= self._max_attempts:
                saga.current_stages.discard(stage.id)
                saga.status = "failed"
                self._store.save(saga)
                self._surface(saga, stage.id, "stage run repeatedly failed", outcome.detail)
                return
            # Retry idempotently: same topology, same payload — no advance happened.

        await self._apply_outcome(saga, stage, outcome)

    async def _apply_outcome(self, saga: SagaState, stage: Stage, outcome: StageRunOutcome) -> None:
        if outcome.status == "completed":
            await self._complete_stage(saga, stage)
        elif outcome.status == "parked":
            saga.status = "parked"
            saga.pending_gate = stage.gate
            saga.pending_gate_stage = stage.id
            self._log(saga, stage.id, "stage.parked-gate", f"awaiting gate {stage.gate}")
            self._store.save(saga)
        elif outcome.status == "rejected":
            saga.current_stages.discard(stage.id)
            saga.status = "failed"
            await self._release_locks(saga, tuple(stage.locks))
            self._log(saga, stage.id, "stage.rejected", outcome.detail)
            self._store.save(saga)
            self._surface(saga, stage.id, "gate rejected the artifact", outcome.detail)
        elif outcome.status == "denied":
            saga.current_stages.discard(stage.id)
            saga.status = "failed"
            await self._release_locks(saga, tuple(stage.locks))
            self._log(saga, stage.id, "stage.denied", outcome.detail)
            self._store.save(saga)
            self._surface(saga, stage.id, "IAM denied the stage", outcome.detail)

    async def _complete_stage(self, saga: SagaState, stage: Stage) -> None:
        saga.current_stages.discard(stage.id)
        saga.pending_gate = None
        saga.pending_gate_stage = None
        if stage.id not in saga.passed_stages:
            saga.passed_stages.append(stage.id)
        self._log(saga, stage.id, "stage.completed", "clean completion")

        next_route = self._graph.route(stage.success) if stage.success is not None else None
        if stage.success is None or next_route is None:
            saga.status = "done"
            self._log(saga, stage.id, "saga.done", "terminal stage completed")
            self._store.save(saga)
            return

        if stage.success in self._external:
            # The next stage's entry is an external event — the controller does not fabricate
            # it; it waits for the webhook or reconciliation (design build.ready-in-qa case).
            saga.status = "active"
            self._log(
                saga, stage.id, "signal.external-wait", f"{stage.success} awaited from source"
            )
            self._store.save(saga)
            return

        self._store.save(saga)
        await self._emit(saga, stage.success)

    async def _emit(self, saga: SagaState, event_name: str) -> None:
        """Emit a stage's ``success`` as an internal inbound signal (the fast path)."""
        self._seq += 1
        event = InboundEvent(
            correlation_id=saga.correlation_id,
            event=event_name,
            source_event_id=f"signal-{self._seq}",
            payload=f"<{event_name} for {saga.correlation_id}>",
        )
        await self.handle_event(event)

    # ---- locks ---------------------------------------------------------------------------

    async def _release_for_event(self, saga: SagaState, event_name: str) -> None:
        lock_ids = saga.lock_release_triggers.pop(event_name, None)
        if lock_ids:
            await self._release_locks(saga, tuple(lock_ids), trigger=event_name)

    async def _release_locks(
        self, saga: SagaState, lock_ids: tuple[str, ...], *, trigger: str | None = None
    ) -> None:
        to_release = tuple(lid for lid in lock_ids if lid in saga.held_locks)
        if not to_release:
            return
        resumed = self._locks.release(saga.correlation_id, to_release)
        saga.held_locks.difference_update(to_release)
        why = f" on {trigger}" if trigger else ""
        self._log(saga, None, "lock.released", f"{sorted(to_release)}{why}")
        self._store.save(saga)
        for rid in resumed:
            await self._resume(rid)

    async def _resume(self, correlation_id: str) -> None:
        saga = self._store.get(correlation_id)
        if saga is None or saga.pending_lock_stage is None:
            return
        stage = self._graph.stage(saga.pending_lock_stage)
        payload = saga.pending_lock_payload
        self._log(saga, stage.id, "stage.resumed", "contended lock freed")
        await self._start_stage(saga, stage, payload, is_loop=False)

    # ---- side-effect helpers -------------------------------------------------------------

    def _close_gate(self, saga: SagaState, stage_id: str | None, gate: str) -> None:
        if self._on_close_gate is not None:
            self._on_close_gate(saga.correlation_id, gate)
        self._log(saga, stage_id, "gate.closed", gate)

    def _surface(self, saga: SagaState, stage_id: str, reason: str, detail: str) -> None:
        self._log(saga, stage_id, "surface.human", reason)
        if self._on_surface is not None:
            self._on_surface(
                SurfaceNotice(
                    correlation_id=saga.correlation_id,
                    stage_id=stage_id,
                    reason=reason,
                    detail=detail,
                )
            )

    def _log(self, saga: SagaState, stage_id: str | None, kind: str, detail: str) -> None:
        self._log_seq += 1
        saga.timeline.append(
            TimelineEntry(
                seq=self._log_seq,
                at=now(),
                correlation_id=saga.correlation_id,
                stage_id=stage_id,
                kind=kind,
                detail=detail,
            )
        )


__all__ = ["DEFAULT_MAX_ATTEMPTS", "PipelineController"]
