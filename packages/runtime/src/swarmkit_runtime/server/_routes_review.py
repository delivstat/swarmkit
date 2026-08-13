"""HTTP review-queue endpoints — the shared surface for resolving harness gates.

The CLI (`swarmkit review …`), the serve web UI, and the fleet UI all resolve the same §6.2
permission and §6.3 input gates through this one API over the same on-disk ``ReviewQueue`` — so a
harness approval behaves identically whichever front-end an operator uses. Read + human-decision
only; the queue is append-only from the agent's perspective (invariant #4).

``POST /review/{id}/resolve`` handles the fourth item kind — a multi-party approval role-task — and
differs from approve/reject in that *who* resolved it is load-bearing: the approval engine checks
the resolver against the workspace role registry. That identity is the authenticated caller, never
a request-body field (design/details/pipeline-gate-approval-ui.md).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.governance._approval import ApprovalPolicy, GateStatus, evaluate
from swarmkit_runtime.review import FileReviewQueue, ReviewItem
from swarmkit_runtime.review._multiparty import collect_resolutions, membership_error
from swarmkit_runtime.server._helpers import _get_runtime


class AnswerRequest(BaseModel):
    answer: str
    comment: str = ""


class CommentRequest(BaseModel):
    """A bare approve/reject on a harness gate, optionally with the reviewer's reasoning."""

    comment: str = ""


class ResolveRequest(BaseModel):
    """Resolve a multi-party approval role-task.

    Deliberately carries no ``identity``: the resolver is the authenticated caller
    (``request.state.identity.client_id``). A body-supplied identity would make every check in the
    approval engine self-asserted — one operator could satisfy an N-of-N policy by resolving each
    role-task under a different name (design/details/pipeline-gate-approval-ui.md).
    """

    outcome: Literal["approve", "changes-requested", "reject"]
    #: What the reviewer said. Relayed to the agent and recorded on the audit; may be empty.
    #: `changes-requested` without one is allowed but unhelpful — the surfaces nudge for it.
    comment: str = ""


_OUTCOME_TO_STATUS: dict[str, Literal["approved", "rejected", "changes-requested"]] = {
    "approve": "approved",
    "reject": "rejected",
    "changes-requested": "changes-requested",
}

_KINDS = {
    "harness-approval": "permission",
    "harness-input": "input",
    "multi-party-approval": "role_task",
}


def _item_to_dict(item: ReviewItem) -> dict[str, Any]:
    """Serialize a review item for a front-end, surfacing the fields each kind needs to render +
    resolve: ``capability`` for a §6.2 permission, ``question``/``options`` for a §6.3 input, and
    ``gate_id``/``role``/``scope``/``rule_index`` for a multi-party role-task.

    A role-task without its gate/role/scope cannot be grouped by gate or told apart from its
    siblings, so a UI could not say *which capacity* the approver is acting in — which is the whole
    content of the decision (design/details/pipeline-gate-approval-ui.md).
    """
    return {
        "id": item.id,
        "kind": _KINDS.get(item.skill_id, "other"),
        "agent_id": item.agent_id,
        "topology_id": item.topology_id,
        "skill_id": item.skill_id,
        "reason": item.reason,
        "status": item.status,
        "answer": item.answer,
        "capability": item.output.get("capability", ""),
        "question": item.output.get("question", ""),
        "options": item.output.get("options", []),
        "free_text_allowed": item.output.get("free_text_allowed", True),
        # Multi-party role-task fields. `resolved_by` mirrors `answer` under a name that says what
        # it holds for this kind — the identity that cast the resolution, empty while pending.
        "gate_id": item.output.get("gate_id", ""),
        # The run that produced what is being approved. A client should link on THIS rather than
        # split the gate id: the id has two shapes and a reader would have to know which.
        "run_id": item.output.get("run_id", ""),
        "role": item.output.get("role", ""),
        "scope": item.output.get("scope", ""),
        "rule_index": item.output.get("rule_index"),
        "resolved_by": item.resolved_by,
        "comment": item.comment,
        "artifact_ref": item.artifact_ref,
        "round": item.round,
        "timestamp": item.timestamp.isoformat(),
    }


async def _resolve_role_task(
    *,
    runtime: Any,
    signal: Any,
    queue: FileReviewQueue,
    item: ReviewItem,
    outcome: Literal["approve", "changes-requested", "reject"],
    comment: str,
    actor: str,
    reread: Any,
) -> dict[str, Any]:
    """Authorize → check membership → audit → record, mirroring ``_ingress_pipeline_event``.

    ``approvals:resolve`` is a reserved human-identity scope (§8.7), so an agent or webhook token
    can never cast a resolution whatever its serve tier. Every attempt is audited, allowed or
    denied, before the 403 is raised — "who tried to approve what" stays answerable.
    """
    role = str(item.output.get("role", ""))
    scope = str(item.output.get("scope", ""))
    gate_id = str(item.output.get("gate_id", ""))

    decision = await runtime.governance.evaluate_action(
        agent_id=actor,
        action="approvals:resolve",
        scopes_required=frozenset({"approvals:resolve"}),
        context={"gate_id": gate_id, "item_id": item.id, "role": role, "scope": scope},
    )
    denial = decision.reason if not decision.allowed else None
    if denial is None and item.skill_id != "multi-party-approval":
        denial = f"review item {item.id!r} is not a multi-party approval role-task"
    if denial is None:
        # Fail closed at the surface with the specific reason. An ineligible resolution is
        # otherwise merely ignored by ``evaluate``, leaving the resolver watching a gate that never
        # advances. Under NoneAuthProvider this is what rejects "anonymous" — unless the workspace
        # genuinely lists it as a role member, which keeps local dev workable.
        denial = membership_error(
            runtime.workspace.role_registry, role=role, scope=scope, identity=actor
        )

    await runtime.governance.record_event(
        AuditEvent(
            event_type="approval.role_task_resolved",
            agent_id=actor,
            timestamp=datetime.now(tz=UTC),
            payload={
                "gate_id": gate_id,
                "item_id": item.id,
                "role": role,
                "scope": scope,
                "outcome": outcome,
                "comment": comment,
                "identity": actor,
                "artifact_ref": item.artifact_ref,
                "round": item.round,
                "allowed": denial is None,
                "reason": denial or decision.reason,
            },
            policy_decision="allow" if denial is None else "deny",
            policy_reason=denial or decision.reason,
        )
    )

    if denial is not None:
        raise HTTPException(
            status_code=403, detail=f"{actor} may not resolve {item.id!r}: {denial}"
        )

    status: Literal["approved", "rejected", "changes-requested"] = _OUTCOME_TO_STATUS[outcome]
    queue.record_resolution(item.id, status, actor, comment=comment)

    # The gate resolving is what RESUMES the run: re-evaluate, and when quorum is reached deliver
    # the `gate` event the controller already waits on. This is what makes
    # `swarmkit pipeline advance` revert to break-glass rather than the only way out
    # (design/details/pipeline-gate-convergence.md).
    await _resume_if_gate_resolved(runtime, signal, queue, item, actor)

    result: dict[str, Any] = reread(item.id)
    return result


async def _resume_if_gate_resolved(
    runtime: Any,
    signal: Any,
    queue: FileReviewQueue,
    item: ReviewItem,
    actor: str,
) -> None:
    """Evaluate the gate this role-task belongs to; on APPROVED/REJECTED, signal the run.

    No-op when the gate is still pending, when its funnel is not resolvable (an externally-driven
    gate), or when no pipeline signal sink is configured — resolution still records either way, so a
    gate never becomes unresolvable because the sink is absent.
    """
    gate_id = str(item.output.get("gate_id", ""))
    funnel_id = str(item.output.get("funnel_id", ""))
    if not gate_id or not funnel_id or signal is None:
        return

    funnel = runtime.workspace.funnels.get(funnel_id)
    approve = (funnel.spec.get("approve") if funnel is not None else None) or None
    if approve is None:
        return
    try:
        policy = ApprovalPolicy.from_dict(approve)
    except (KeyError, TypeError, ValueError):
        return

    ev = evaluate(
        policy,
        runtime.workspace.role_registry,
        collect_resolutions(queue, gate_id=gate_id, policy=policy),
    )
    if ev.status is GateStatus.CHANGES_REQUESTED:
        # Rework: the stage runs AGAIN with the comments in its input, rather than the run ending.
        # `rework` (not `gate`) so the controller can tell "try again" from "we are done".
        correlation_id, _, stage = gate_id.rpartition(":")
        await runtime.governance.record_event(
            AuditEvent(
                event_type="approval.changes_requested",
                agent_id=actor,
                timestamp=datetime.now(tz=UTC),
                payload={
                    "gate_id": gate_id,
                    "stage": stage,
                    "artifact_ref": item.artifact_ref,
                    "round": item.round,
                    "comment": item.comment,
                },
            )
        )
        await signal(correlation_id, json.dumps({"kind": "rework", "stage": stage}))
        return
    if ev.status is not GateStatus.APPROVED and ev.status is not GateStatus.REJECTED:
        return

    # gate_id is `<correlation_id>:<stage>`; split on the LAST colon so a correlation id that
    # itself contains one still resolves.
    correlation_id, _, stage = gate_id.rpartition(":")
    approved = ev.status is GateStatus.APPROVED
    await runtime.governance.record_event(
        AuditEvent(
            event_type="approval.gate_resolved",
            agent_id=actor,
            timestamp=datetime.now(tz=UTC),
            payload={
                "gate_id": gate_id,
                "status": ev.status.value,
                "approvers": sorted(ev.distinct_approvers),
                "resumed": True,
            },
        )
    )
    await signal(
        correlation_id,
        json.dumps({"kind": "gate", "approved": approved, "stage": stage}),
    )


def _register_review_routes(app: FastAPI, workspace_path: Path) -> None:
    """GET /review[/all], GET /review/{id}, POST /review/{id}/(approve|reject|answer)."""

    def _queue() -> FileReviewQueue:
        return FileReviewQueue(workspace_path)

    def _find(queue: FileReviewQueue, item_id: str) -> ReviewItem:
        item = queue.get(item_id)
        if item is None:  # convenience: accept an id prefix, like the CLI
            matches = [i for i in queue.list_all() if i.id.startswith(item_id)]
            item = matches[0] if matches else None
        if item is None:
            raise HTTPException(status_code=404, detail=f"review item {item_id!r} not found")
        return item

    def _filtered(items: list[ReviewItem], kind: str, gate_id: str) -> list[dict[str, Any]]:
        out = [_item_to_dict(i) for i in items]
        if kind:
            out = [i for i in out if i["kind"] == kind]
        if gate_id:
            out = [i for i in out if i["gate_id"] == gate_id]
        return out

    @app.get("/review")
    async def list_pending(kind: str = "", gate_id: str = "") -> list[dict[str, Any]]:
        """Pending items, optionally narrowed to one ``kind`` and/or one gate.

        The gate filter is what lets an inbox group a parked run's role-tasks without fetching the
        whole queue and grouping client-side.
        """
        return _filtered(_queue().list_pending(), kind, gate_id)

    @app.get("/review/all")
    async def list_all(kind: str = "", gate_id: str = "") -> list[dict[str, Any]]:
        return _filtered(_queue().list_all(), kind, gate_id)

    @app.get("/gates/{gate_id}")
    async def gate_state(gate_id: str, request: Request, author: str = "") -> dict[str, Any]:
        """A gate's state with its approval policy applied.

        The endpoint an external driver polls: `GET /review?gate_id=…` returns role-tasks, and
        deciding whether they add up to an approval means reimplementing quorum, distinct-approver
        counting and exclude_author (design/details/gate-state-and-deferring-approval.md).

        ``author`` is optional and only matters when the policy excludes the author; when it is
        needed and absent the response says so rather than quietly evaluating without it.
        """
        from swarmkit_runtime.gate_state import (  # noqa: PLC0415
            PolicyUnresolvedError,
            UnknownGateError,
            compute_gate_state,
        )

        runtime = _get_runtime(request)
        try:
            state = compute_gate_state(
                _queue(),
                runtime.workspace.role_registry,
                runtime.workspace,
                gate_id,
                author=author or None,
            )
        except UnknownGateError as exc:
            raise HTTPException(status_code=404, detail=f"No gate {gate_id!r}") from exc
        except PolicyUnresolvedError as exc:
            # 409, not 500: the gate is real and the workspace is the thing that cannot answer for
            # it. A 500 would read as "SwarmKit broke" rather than "this funnel is not here".
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return dict(state.to_dict())

    @app.get("/review/{item_id}")
    async def get_item(item_id: str) -> dict[str, Any]:
        return _item_to_dict(_find(_queue(), item_id))

    @app.post("/review/{item_id}/approve")
    async def approve(item_id: str, body: CommentRequest | None = None) -> dict[str, Any]:
        queue = _queue()
        item = _find(queue, item_id)
        queue.resolve(item.id, "approved", (body.comment if body else ""))
        return _item_to_dict(_find(queue, item.id))

    @app.post("/review/{item_id}/reject")
    async def reject(item_id: str, body: CommentRequest | None = None) -> dict[str, Any]:
        queue = _queue()
        item = _find(queue, item_id)
        queue.resolve(item.id, "rejected", (body.comment if body else ""))
        return _item_to_dict(_find(queue, item.id))

    @app.post("/review/{item_id}/answer")
    async def answer(item_id: str, body: AnswerRequest) -> dict[str, Any]:
        queue = _queue()
        item = _find(queue, item_id)
        # a bare integer selects an option index; else the text is used verbatim
        resolved = body.answer
        options = item.output.get("options") or []
        if body.answer.isdigit() and 0 <= int(body.answer) < len(options):
            resolved = str(options[int(body.answer)])
        queue.answer_input(item.id, resolved, body.comment)
        return _item_to_dict(_find(queue, item.id))

    @app.post("/review/{item_id}/resolve")
    async def resolve_multiparty_task(
        item_id: str, body: ResolveRequest, request: Request
    ) -> dict[str, Any]:
        """Resolve a multi-party approval role-task as the authenticated caller."""
        queue = _queue()
        return await _resolve_role_task(
            runtime=_get_runtime(request),
            signal=getattr(request.app.state, "pipeline_signal", None),
            queue=queue,
            item=_find(queue, item_id),
            outcome=body.outcome,
            comment=body.comment,
            actor=getattr(getattr(request.state, "identity", None), "client_id", None)
            or "anonymous",
            reread=lambda item_id: _item_to_dict(_find(queue, item_id)),
        )
