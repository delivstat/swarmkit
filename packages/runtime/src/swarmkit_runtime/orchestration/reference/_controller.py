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
        stage_result: Any = None,
    ) -> None:
        self._run = run_stage
        self._store = store
        self._graphs = graphs
        #: Optional seam: ``(correlation_id, stage_id) -> StageOutcome | None``, answering "did
        #: this stage already finish?" from the durable job record. Without it a stage that
        #: completed while the orchestrator was down is unrecoverable — see `_reconcile`.
        self._stage_result = stage_result

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
        if isinstance(data, dict) and data.get("kind") == "rework":
            await self._rework(correlation_id, data)
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
            # Say WHY, and at warning: an event accepted and dropped with no trace reads exactly
            # like an event that ran, and a stale artifact then looks like a fresh result.
            existing = self._store.get(correlation_id)
            # Before dropping: did the stage this saga is waiting on already FINISH?
            #
            # The lease makes a stranded event reachable again (bug 12), and reaching it was not
            # enough. If the orchestrator died between "serve finished the stage" and "the saga
            # recorded it", the reclaim delivers the event here and this branch discarded it — the
            # work was done, paid for and sitting in `jobs`, while the saga stayed `active` with an
            # empty `passed_stages` and an `updated_at` frozen at creation. Every individual record
            # said success; the run never moved again, and no timeout or restart could move it.
            if await self._reconcile(existing):
                return
            logger.warning(
                "pipeline event %r for existing saga %r DROPPED (status=%s%s). The bundled "
                "controller advances sequentially; %s — re-emitting does nothing.",
                name,
                correlation_id,
                getattr(existing, "status", "unknown"),
                f", parked on {existing.pending_gate_stage!r}"
                if existing is not None and existing.pending_gate_stage
                else "",
                # The remedy has to match the state. "resolve or clear the gate" was printed
                # whenever a saga existed, so a reader whose saga had no gate went looking for one
                # that was not there.
                "resolve or clear the gate to resume"
                if existing is not None and existing.pending_gate_stage
                else "this run is already in progress",
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
        existing = self._store.get(correlation_id)
        if existing is not None:
            # Idempotent, but not silent — a repeated start that quietly no-ops is the same false
            # reading as above.
            logger.warning(
                "start for correlation %r DROPPED: a saga already exists (status=%s). "
                "Use a fresh correlation id to run again.",
                correlation_id,
                existing.status,
            )
            return
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

    async def _rework(self, correlation_id: str, data: dict[str, Any]) -> None:
        """A reviewer asked for changes: re-run the parked stage instead of ending the run.

        The distinction from a reject is the whole point — reject is terminal, changes-requested is
        another attempt. The stage's input picks up the reviewer's comments, so the re-run is not a
        repeat of the same work (design/details/human-decision-comments.md).
        """
        saga = self._store.get(correlation_id)
        if saga is None or saga.status != "parked":
            logger.warning(
                "rework event for correlation %r DROPPED: %s",
                correlation_id,
                "no such saga" if saga is None else f"saga is {saga.status}, not parked",
            )
            return
        stage = saga.pending_gate_stage
        saga.pending_gate_stage = None
        saga.status = "active"
        saga.attempts[str(stage)] = saga.attempts.get(str(stage), 0) + 1

        # The comment is the whole reason this is a rework and not a re-run. It used to be dropped:
        # `data["detail"]` was never read and the fixed string below overwrote the timeline detail,
        # so the reviewer's reason reached neither the agent nor a later human reader. The stage
        # then reproduced substantially the same output and the reviewer had no way to tell why.
        #
        # This path is the break-glass one: when serve runs `auth: none`, `/review/{item}/resolve`
        # 403s (`approvals:resolve` is reserved for human identity) and callers fall back to
        # enqueuing this event, so the comment travels only here. Losing it made comment delivery
        # depend on the auth provider, with nothing different for the reviewer to see.
        comment = str(data.get("detail", "") or "").strip()
        note = "changes requested — re-running the stage"
        saga.add(
            "resumed",
            stage_id=stage,
            detail=f"{note}: {comment}" if comment else note,
            # Stamped with the round and the artifact it was written against, so the re-run can tell
            # a note about the revision it just wrote from one about the draft two rounds ago.
            meta={
                "comment": comment,
                "round": saga.attempts.get(str(stage), 0),
                "artifact_ref": saga.artifacts.get(str(stage), ""),
                "identity": str(data.get("identity", "") or "operator-override"),
            }
            if comment
            else {},
        )
        if not comment:
            # Not silent: a rework with no reason is a legitimate action, but it is also what a
            # dropped comment looks like, and those two must not be indistinguishable in the log.
            logger.info(
                "rework for correlation %r carries no comment — the re-run will have no reviewer "
                "feedback to act on",
                correlation_id,
            )
        self._store.save(saga)
        await self._drive(saga)

    async def _resolve_gate(self, correlation_id: str, data: dict[str, Any]) -> None:
        saga = self._store.get(correlation_id)
        if saga is None or saga.status != "parked":
            logger.warning(
                "gate event for correlation %r DROPPED: %s",
                correlation_id,
                "no such saga" if saga is None else f"saga is {saga.status}, not parked",
            )
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
    async def _reconcile(self, saga: SagaState | None) -> bool:
        """Absorb a stage that finished while nobody was listening. True if the saga moved.

        A saga is stranded when the orchestrator dies between a stage completing and the saga
        recording it: `current_stage` is set, `passed_stages` is empty, and the reclaimed event is
        then dropped as a duplicate — correctly, in general, because a second `ticket.created` for
        a live saga must not start a second run. It is wrong only here, where the saga never
        absorbed a stage that did in fact complete.

        Nothing new is stored to make this work. `jobs` is already keyed `<correlation>:<stage>`
        and holds the stage's status, output and cost, so "waiting on X, and X completed" is a
        question the existing record can answer.

        Conservative on purpose: only an ACTIVE saga, only one with a `current_stage` and no gate,
        and only when the job says `completed`. A stage that failed, is still running, or was never
        recorded leaves the saga exactly as it was — recovering a run that did not finish would be
        a worse error than leaving it stuck.
        """
        if self._stage_result is None or saga is None:
            return False
        if saga.status != "active" or not saga.current_stage or saga.pending_gate_stage:
            return False
        sid = saga.current_stage
        if sid in saga.passed_stages:
            return False
        outcome = self._stage_result(saga.correlation_id, sid, saga.attempts.get(sid, 1))
        if outcome is None or getattr(outcome, "status", "") != "completed":
            return False

        # A GATED stage is never absorbed. The stage finished, but what did not happen is the
        # review, and opening a gate needs the workspace funnels, the review queue and the artifact
        # — all serve-side. Absorbing here could only ever synthesise "completed", so the saga
        # advanced straight past an approval the pipeline declares as required and the next stage
        # ran unreviewed. That is the QUIET direction of failure: the stranding this recovery
        # exists to fix is visible as a run that never progresses, while this looks like a run that
        # went fine.
        #
        # So gated stages keep the stranding, loudly, and a human releases them. Automating past a
        # human decision is not a recovery.
        if self._declares_a_gate(saga, sid):
            logger.warning(
                "saga %r is waiting on stage %r, which has already completed — NOT absorbing it, "
                "because the stage declares a gate and the approval has not happened. The work is "
                "done (see the job record); release it with `swarmkit pipeline advance %s` after "
                "review, or resolve the gate.",
                saga.correlation_id,
                sid,
                saga.correlation_id,
            )
            # On the saga's own timeline, so `pipeline status` says why it is not moving rather
            # than leaving an operator to infer it from silence. Once, not once per redelivery —
            # the lease keeps handing this event back, and a timeline that scrolls is a timeline
            # nobody reads.
            if not any(e.kind == "blocked" and e.stage_id == sid for e in saga.timeline):
                saga.add(
                    "blocked",
                    stage_id=sid,
                    detail=(
                        "stage completed while the orchestrator was down; "
                        "its gate still needs a human"
                    ),
                )
                self._store.save(saga)
            return False

        logger.warning(
            "saga %r was waiting on stage %r, which had already completed — absorbing it. The "
            "orchestrator was down when the stage finished; without this the run would stay "
            "`active` for ever with the work done and paid for.",
            saga.correlation_id,
            sid,
        )
        saga.passed_stages.append(sid)
        if getattr(outcome, "artifact", ""):
            saga.artifacts[sid] = outcome.artifact
        saga.current_stage = None
        saga.add("completed", stage_id=sid, detail="reconciled after an orchestrator restart")
        self._store.save(saga)
        await self._drive(saga)
        return True

    def _declares_a_gate(self, saga: SagaState, stage_id: str) -> bool:
        """Whether the named stage of this saga's graph declares a human gate."""
        for index, stage in enumerate(self._graphs.get(saga.graph_id, {}).get("stages") or []):
            if _stage_id(stage, index) == stage_id:
                return bool(stage.get("gate") or stage.get("funnel"))
        # An unknown stage is not assumed ungated: absorbing one whose spec cannot be read would be
        # deciding on missing information, in the direction that skips reviews.
        return True

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
                # Record the artifact size: a FAILED stage parks exactly like a successful one, and
                # the two render identically. A real record is kilobytes; a harness failure is a
                # 46-character error string. The size is the cheapest signal that tells them apart.
                detail = outcome.detail or ""
                if outcome.artifact_bytes is not None:
                    detail = f"{detail} (artifact {outcome.artifact_bytes} bytes)".strip()
                saga.add("parked", stage_id=sid, detail=detail)
                self._store.save(saga)
                return  # wait for a `gate` event
            # rejected | denied | failed — terminal for this saga (surfaced to the human).
            saga.status = "failed" if outcome.status in ("failed", "denied") else "rejected"
            saga.current_stage = None
            saga.add(saga.status, stage_id=sid, detail=outcome.detail)
            self._store.save(saga)
            return


__all__ = ["ReferenceController", "StageOutcome"]
