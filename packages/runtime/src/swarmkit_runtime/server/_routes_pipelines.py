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
- ``POST /pipelines/signal`` — the ingress front door (design/details/pipeline-triggering.md): turn
  an authorised outside event into a structured ``(correlation_id, event)`` and hand it to the
  injected signal sink (``app.state.pipeline_signal``, a ``PipelineSignal``). Guarded: ``advance`` /
  ``skip`` are operator acts requiring a reserved human-identity scope through the
  GovernanceProvider; every attempt is audited; delivery is 503 when the sink is unset. The same
  guardrail backs the ``submit_pipeline_event`` MCP tool.

The actual stage execution is the injected drive seam (``app.state.pipeline_run_stage``, a
``RunStage``): production wires it to the StageRunner over a run context; tests/demos inject a
scripted stub, mirroring how the orchestrator demos inject ``run_stage``. The handler stays thin.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.governance import AuditEvent, GovernanceProvider
from swarmkit_runtime.governance._approval import ApprovalPolicy, GateStatus, evaluate
from swarmkit_runtime.orchestration import PipelineSignal, RunStage, StageOutcome
from swarmkit_runtime.review import FileReviewQueue, ReviewItem
from swarmkit_runtime.review._multiparty import collect_resolutions

from ._helpers import _get_runtime

# The three ingress modes (design/details/pipeline-triggering.md §"The governance guardrail"):
# ``emit`` is an ordinary authorised event (the serve ``run`` tier suffices); ``advance`` / ``skip``
# start or jump a stage mid-saga and are **operator acts** requiring the matching reserved
# human-identity scope through the GovernanceProvider — never a transport-token capability.
PipelineMode = Literal["emit", "advance", "skip"]
_OPERATOR_MODES: frozenset[str] = frozenset({"advance", "skip"})


class RunStageRequest(BaseModel):
    """Drive one stage: an opaque correlation id + the stage spec to run."""

    correlation_id: str
    stage: dict[str, Any]


class StageOutcomeResponse(BaseModel):
    """A :class:`StageOutcome` on the wire."""

    status: str
    artifact: str = ""
    detail: str = ""
    #: Size of the produced artifact — the cheapest signal separating a stage that parked with a
    #: real record from one that parked with a 46-character failure string.
    artifact_bytes: int | None = None


class RoleTaskSummary(BaseModel):
    """One role-task of a gate — what a UI needs to render the decision and who made it."""

    id: str
    role: str
    scope: str
    rule_index: int | None = None
    status: str
    resolved_by: str = ""


class GateStatusResponse(BaseModel):
    """The resolution of a funnel gate, learned by an external orchestrator.

    ``items`` carries the per-role detail an approval UI needs; the aggregate ``status`` alone
    cannot say which roles are outstanding. ``quorum_evaluated`` reports *how* ``status`` was
    derived — see the endpoint docstring, because the two answers can differ.
    """

    correlation_id: str
    gate: str
    status: Literal["approved", "rejected", "pending"]
    items: list[RoleTaskSummary] = []
    quorum_evaluated: bool = False


class PipelineSignalRequest(BaseModel):
    """One structured pipeline event, submitted through the ingress front door.

    Domain-neutral: ``correlation_id`` is an opaque handle (never a business id), ``event`` is the
    structured event to signal, ``source_event_id`` is passed through for the orchestrator's dedup
    (the runtime keeps no dedup state), and ``mode`` selects the guardrail — ``emit`` (default) is
    an ordinary authorised event; ``advance`` / ``skip`` are operator acts gated on a reserved
    human-identity scope.
    """

    correlation_id: str
    event: str
    source_event_id: str | None = None
    mode: PipelineMode = "emit"


class PipelineSignalResponse(BaseModel):
    """Acknowledgement that an authorised event was audited and delivered to the signal seam."""

    delivered: bool
    correlation_id: str
    event: str
    mode: PipelineMode
    source: str


class PipelineIngressError(Exception):
    """A guardrail outcome the ingress could not satisfy — carries the HTTP status the endpoint
    maps to (403 authorization denied, 503 signal sink unconfigured). The audit record, when one is
    warranted (the authorization decision), is written *before* this is raised."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def _ingress_pipeline_event(
    *,
    governance: GovernanceProvider,
    signal: PipelineSignal | None,
    correlation_id: str,
    event: str,
    mode: PipelineMode,
    actor_identity: str,
    source: str,
    source_event_id: str | None,
) -> None:
    """The single, load-bearing ingress path shared by the HTTP endpoint and the MCP tool.

    Authorize → audit → deliver, in that order:

    1. **Authorize.** ``advance`` / ``skip`` are operator acts: the *caller's identity* must hold
       the matching reserved scope (``pipeline:advance`` / ``pipeline:skip``) via the
       GovernanceProvider — a human-identity act, structurally un-grantable to a transport/agent
       token (design §8.7). ``emit`` is authorised by the normal serve ``run`` tier and needs no
       governance grant.
    2. **Audit.** Every ingress attempt — allowed *or* denied — is recorded on the append-only
       audit, stamped with the ``source`` and ``(correlation_id, event, mode)`` and the pass-through
       ``source_event_id``, so "who advanced X, and why" is answerable.
    3. **Deliver.** Hand the ``(correlation_id, event)`` to the injected signal sink. Dedup and
       sequencing are the orchestrator's job; the runtime keeps no dedup state.

    Raises :class:`PipelineIngressError` (403) when authorization is denied — after auditing the
    denial — and (503) when the signal sink is unconfigured (sanctioned, like run-stage).
    """
    allowed = True
    reason = "emit authorised by the serve run tier"
    if mode in _OPERATOR_MODES:
        scope = f"pipeline:{mode}"
        decision = await governance.evaluate_action(
            agent_id=actor_identity,
            action=scope,
            scopes_required=frozenset({scope}),
            context={"source": source, "correlation_id": correlation_id, "event": event},
        )
        allowed = decision.allowed
        reason = decision.reason

    await governance.record_event(
        AuditEvent(
            event_type="pipeline.ingress",
            agent_id=actor_identity,
            timestamp=datetime.now(tz=UTC),
            payload={
                "correlation_id": correlation_id,
                "event": event,
                "mode": mode,
                "source": source,
                "source_event_id": source_event_id,
                "allowed": allowed,
                "reason": reason,
            },
            policy_decision="allow" if allowed else "deny",
            policy_reason=reason,
        )
    )

    if not allowed:
        raise PipelineIngressError(
            403,
            f"{source} is not authorised to {mode} pipeline event "
            f"{event!r} for {correlation_id!r}: {reason}",
        )

    if signal is None:
        raise PipelineIngressError(
            503,
            "pipeline signal seam not configured "
            "(set app.state.pipeline_signal to a PipelineSignal)",
        )

    await signal(correlation_id, event)


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


def _find_agent_funnel(workspace: Any, agent_id: str) -> Any | None:
    """The resolved funnel of the agent whose gate this is, or None.

    A gate is opened as ``f"{correlation_id}:{agent_id}"`` by the StageRunner, so the agent id is
    the gate name. Walks every topology's agent tree because an agent is addressed by id, not path.
    """

    def walk(agent: Any) -> Any | None:
        if agent.id == agent_id:
            return agent.funnel
        for child in agent.children:
            found = walk(child)
            if found is not None:
                return found
        return None

    for topology in workspace.topologies.values():
        funnel = walk(topology.root)
        if funnel is not None:
            return funnel
    return None


def _gate_policy(
    workspace: Any, gate: str, items: list[ReviewItem] | None = None
) -> ApprovalPolicy | None:
    """The ``ApprovalPolicy`` governing *gate*, or None when it cannot be located.

    Two shapes, because gates arrive from two places. A PIPELINE stage gate records its
    ``funnel_id`` on each role-task when it opens (pipeline-gate-convergence.md), so the funnel is
    read straight off the item — no walk needed, and it works for a gate whose name is a stage id
    rather than an agent id. An in-topology AGENT funnel has no such record, so fall back to
    walking the agent tree by the gate name.
    """
    funnel_id = next(
        (str(i.output.get("funnel_id", "")) for i in (items or []) if i.output.get("funnel_id")),
        "",
    )
    funnel = workspace.funnels.get(funnel_id) if funnel_id else _find_agent_funnel(workspace, gate)
    approve = (funnel.spec.get("approve") if funnel is not None else None) or None
    if approve is None:
        return None
    try:
        return ApprovalPolicy.from_dict(approve)
    except (KeyError, TypeError, ValueError):
        return None


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
            status=outcome.status,
            artifact=outcome.artifact,
            detail=outcome.detail,
            artifact_bytes=outcome.artifact_bytes,
        )

    @app.post("/pipelines/signal")
    async def signal_pipeline(
        body: PipelineSignalRequest, request: Request
    ) -> PipelineSignalResponse:
        runtime = _get_runtime(request)
        identity = getattr(request.state, "identity", None)
        actor = getattr(identity, "client_id", None) or "anonymous"
        source = f"api:{actor}"
        seam: PipelineSignal | None = getattr(request.app.state, "pipeline_signal", None)
        try:
            await _ingress_pipeline_event(
                governance=runtime.governance,
                signal=seam,
                correlation_id=body.correlation_id,
                event=body.event,
                mode=body.mode,
                actor_identity=actor,
                source=source,
                source_event_id=body.source_event_id,
            )
        except PipelineIngressError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return PipelineSignalResponse(
            delivered=True,
            correlation_id=body.correlation_id,
            event=body.event,
            mode=body.mode,
            source=source,
        )

    @app.get("/pipelines/gate-status/{correlation_id}/{gate}")
    async def gate_status(correlation_id: str, gate: str, request: Request) -> GateStatusResponse:
        """Report a gate's resolution + its per-role detail.

        ``status`` is evaluated through the **approval engine** when the gate's ``ApprovalPolicy``
        can be located (``quorum_evaluated: true``) — the same `evaluate()` the runtime gates on, so
        the report agrees with the decision. Folding the items directly cannot do this: it takes
        every task approving as the bar, which is right only for ``quorum: all``. Under
        ``quorum: any`` the engine approves on the first resolution while the fold still says
        pending, and an orchestrator polling this would wait for a gate that had already opened.

        The fold remains the fallback (``quorum_evaluated: false``) for a gate whose policy is not
        reachable from this workspace — an externally-driven gate, or one whose agent has since been
        renamed.
        """
        queue = FileReviewQueue(workspace_path)
        items = _gate_items(queue, correlation_id, gate)

        status = _aggregate_gate_status(items)
        quorum_evaluated = False
        # Not _get_runtime: gate-status must keep answering before the workspace has loaded (it
        # falls back to the fold), rather than 503 an orchestrator that is only polling.
        runtime: WorkspaceRuntime | None = getattr(request.app.state, "runtime", None)
        workspace = runtime.workspace if runtime is not None else None
        policy = _gate_policy(workspace, gate, items) if workspace is not None else None
        if policy is not None and workspace is not None and items:
            gate_id = f"{correlation_id}:{gate}"
            ev = evaluate(
                policy,
                workspace.role_registry,
                collect_resolutions(queue, gate_id=gate_id, policy=policy),
            )
            if ev.status is GateStatus.APPROVED:
                status = "approved"
            elif ev.status is GateStatus.REJECTED:
                status = "rejected"
            else:
                status = "pending"
            quorum_evaluated = True

        return GateStatusResponse(
            correlation_id=correlation_id,
            gate=gate,
            status=status,
            items=[
                RoleTaskSummary(
                    id=i.id,
                    role=str(i.output.get("role", "")),
                    scope=str(i.output.get("scope", "")),
                    rule_index=i.output.get("rule_index"),
                    status=i.status,
                    resolved_by=i.answer,
                )
                for i in items
            ],
            quorum_evaluated=quorum_evaluated,
        )
