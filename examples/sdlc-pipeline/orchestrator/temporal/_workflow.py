"""The data-driven pipeline workflow — one Temporal workflow interprets any StageGraph.

Sandbox-clean by construction: the workflow routes over the plain graph **dict** with pure
functions and touches the outside world only through activities (``run_pipeline_stage``) and
signals (gate resolutions + external events). No wall-clock, randomness, or I/O in workflow code —
Temporal replays it deterministically, so a parked pipeline is a suspended workflow, not a polled
row. See design/details/orchestration-provider-seam.md.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow

# The stage-run activity is registered by the adapter (it holds the run_stage seam); the workflow
# invokes it by name so the workflow module never imports the seam or the runtime.
_RUN_STAGE = "run_pipeline_stage"
_STAGE_TIMEOUT = timedelta(hours=1)


def _stage(graph: dict[str, Any], stage_id: str) -> dict[str, Any] | None:
    for stage in graph.get("stages") or []:
        if stage.get("id") == stage_id:
            return stage
    return None


def _route(graph: dict[str, Any], event: str) -> tuple[dict[str, Any], bool] | None:
    """Route an event to (stage, is_loop). Loops (explicit back-edges) win over forward `when`."""
    for loop in graph.get("loops") or []:
        if loop.get("when") == event:
            target = _stage(graph, str(loop.get("to")))
            if target is not None:
                return target, True
    for stage in graph.get("stages") or []:
        if event in (stage.get("when") or []):
            return stage, False
    return None


@workflow.defn
class PipelineWorkflow:
    """One requirement's pipeline, run as a durable saga (workflow id = requirement id)."""

    def __init__(self) -> None:
        self._graph: dict[str, Any] = {}
        self._req: str = ""
        self._inbox: list[str] = []
        self._gates: dict[str, bool] = {}
        self._passed: list[str] = []
        self._current: str | None = None
        self._pending_gate: str | None = None
        self._status: str = "active"
        self._cancelled: bool = False
        self._done: bool = False

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        self._graph = params["graph"]
        self._req = params["requirement_id"]
        self._inbox.append(params["initial_event"])

        while not self._done:
            await workflow.wait_condition(lambda: bool(self._inbox) or self._cancelled)
            if self._cancelled:
                await self._compensate()
                self._status = "cancelled"
                break
            await self._handle(self._inbox.pop(0))

        self._current = None
        self._pending_gate = None
        return self._view()

    # ---- signals + query (the ingress + gate-resolution seams) ----

    @workflow.signal
    def submit_event(self, event: str) -> None:
        self._inbox.append(event)

    @workflow.signal
    def resolve_gate(self, gate: str, approved: bool) -> None:
        self._gates[gate] = approved

    @workflow.signal
    def cancel(self) -> None:
        self._cancelled = True

    @workflow.query
    def view(self) -> dict[str, Any]:
        return self._view()

    # ---- the saga ----

    async def _handle(self, event: str) -> None:
        routed = _route(self._graph, event)
        if routed is None:
            return
        stage, is_loop = routed
        sid = str(stage["id"])
        if sid in self._passed and not is_loop:
            return  # idempotent forward advance
        if is_loop and sid in self._passed:
            self._passed.remove(sid)  # defect re-entry re-runs a passed stage

        self._current = sid
        self._status = "active"
        outcome: dict[str, Any] = await workflow.execute_activity(
            _RUN_STAGE,
            {"requirement_id": self._req, "stage": stage},
            schedule_to_close_timeout=_STAGE_TIMEOUT,
        )
        status = outcome.get("status")

        if status in ("failed", "rejected", "denied"):
            self._status = "failed" if status != "rejected" else "rejected"
            self._done = True
            return

        if stage.get("gate") and status == "parked":
            gate = str(stage["gate"])
            self._pending_gate = gate
            self._status = "parked"
            await workflow.wait_condition(lambda: gate in self._gates or self._cancelled)
            if self._cancelled:
                return
            self._pending_gate = None
            if not self._gates.pop(gate):
                self._status = "rejected"
                self._done = True
                return

        # completed (ungated, or gate approved)
        self._passed.append(sid)
        self._current = None
        success = stage.get("success")
        if not success or _route(self._graph, str(success)) is None:
            self._status = "done"
            self._done = True
        else:
            self._inbox.append(str(success))  # emit the internal signal (fast path)

    async def _compensate(self) -> None:
        for sid in reversed(self._passed):
            stage = _stage(self._graph, sid)
            comp = stage.get("compensation") if stage else None
            if not comp:
                continue
            await workflow.execute_activity(
                _RUN_STAGE,
                {
                    "requirement_id": self._req,
                    "stage": {"id": sid, "topology": comp},
                    "compensation": True,
                },
                schedule_to_close_timeout=_STAGE_TIMEOUT,
            )

    def _view(self) -> dict[str, Any]:
        return {
            "requirement_id": self._req,
            "status": self._status,
            "current_stage": self._current,
            "passed_stages": list(self._passed),
            "pending_gate": self._pending_gate,
        }
