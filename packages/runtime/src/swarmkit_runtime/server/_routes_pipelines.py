"""The pipeline drive seam over serve (design/details/orchestration-provider-seam.md).

The two runtime-side seams an external orchestrator (the reference controller, Temporal — both in
``examples/sdlc-pipeline/orchestrator/``) drives, exposed as HTTP:

- ``POST /pipelines/run-stage`` — run a stage's topology as one bounded, governed SwarmKit run,
  stamped with an opaque ``correlation_id``; returns a :class:`~swarmkit_runtime.orchestration`
  ``StageOutcome``-shaped body. The runtime models no business instance — it only stamps the
  correlation id so the append-only audit assembles the cross-stage trail.
- ``GET /pipelines/gate-status/{correlation_id}/{gate}`` — report whether a funnel gate has
  resolved (``approved`` / ``rejected`` / ``pending``). The pause itself is a SwarmKit checkpoint
  resumed by humans through ``/review``; this only lets an orchestrator *learn* the result.

The actual stage execution is the injected drive seam (``app.state.pipeline_run_stage``, a
``RunStage``): production wires it to the StageRunner over a run context; tests/demos inject a
scripted stub, mirroring how the orchestrator demos inject ``run_stage``. The handler stays thin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from swarmkit_runtime.orchestration import RunStage, StageOutcome
from swarmkit_runtime.review import FileReviewQueue, ReviewItem


class RunStageRequest(BaseModel):
    """Drive one stage: an opaque correlation id + the stage spec to run."""

    correlation_id: str
    stage: dict[str, Any]


class StageOutcomeResponse(BaseModel):
    """A :class:`StageOutcome` on the wire."""

    status: str
    artifact: str = ""
    detail: str = ""


class GateStatusResponse(BaseModel):
    """The resolution of a funnel gate, learned by an external orchestrator."""

    correlation_id: str
    gate: str
    status: Literal["approved", "rejected", "pending"]


def _gate_ids(correlation_id: str, gate: str) -> frozenset[str]:
    """The gate_id forms a funnel gate may have been opened under.

    The StageRunner opens a stage's gate as ``f"{correlation_id}:{agent_id}"``; a caller may also
    pass the fully-qualified id directly. Accept both so the orchestrator can name the gate either
    way without knowing the runtime's internal composition rule.
    """
    return frozenset({f"{correlation_id}:{gate}", gate})


def _gate_items(queue: FileReviewQueue, correlation_id: str, gate: str) -> list[ReviewItem]:
    """The multi-party role-task items belonging to a gate (matched by their ``gate_id``)."""
    wanted = _gate_ids(correlation_id, gate)
    return [i for i in queue.list_all() if i.output.get("gate_id") in wanted]


def _aggregate_gate_status(items: list[ReviewItem]) -> Literal["approved", "rejected", "pending"]:
    """Fold a gate's role-task items into a single resolution.

    Domain-neutral aggregation over the existing review data: any rejected task rejects the gate;
    an unopened gate or any still-pending task is ``pending``; a gate whose every task approved is
    ``approved``. (Quorum policy lives in the approval engine that resolves the gate; this only
    *reports* what the persisted tasks already say.)
    """
    if not items:
        return "pending"
    if any(i.status == "rejected" for i in items):
        return "rejected"
    if all(i.status == "approved" for i in items):
        return "approved"
    return "pending"


def _register_pipeline_routes(app: FastAPI, workspace_path: Path) -> None:
    """Register the pipeline drive seam (run-stage + gate-status)."""

    @app.post("/pipelines/run-stage")
    async def run_stage(body: RunStageRequest, request: Request) -> StageOutcomeResponse:
        seam: RunStage | None = getattr(request.app.state, "pipeline_run_stage", None)
        if seam is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "pipeline run-stage seam not configured "
                    "(set app.state.pipeline_run_stage to a RunStage)"
                ),
            )
        outcome: StageOutcome = await seam(body.correlation_id, body.stage)
        return StageOutcomeResponse(
            status=outcome.status, artifact=outcome.artifact, detail=outcome.detail
        )

    @app.get("/pipelines/gate-status/{correlation_id}/{gate}")
    async def gate_status(correlation_id: str, gate: str) -> GateStatusResponse:
        queue = FileReviewQueue(workspace_path)
        items = _gate_items(queue, correlation_id, gate)
        return GateStatusResponse(
            correlation_id=correlation_id,
            gate=gate,
            status=_aggregate_gate_status(items),
        )
