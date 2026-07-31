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

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.review import FileReviewQueue, ReviewItem
from swarmkit_runtime.review._multiparty import membership_error
from swarmkit_runtime.server._helpers import _get_runtime


class AnswerRequest(BaseModel):
    answer: str


class ResolveRequest(BaseModel):
    """Resolve a multi-party approval role-task.

    Deliberately carries no ``identity``: the resolver is the authenticated caller
    (``request.state.identity.client_id``). A body-supplied identity would make every check in the
    approval engine self-asserted — one operator could satisfy an N-of-N policy by resolving each
    role-task under a different name (design/details/pipeline-gate-approval-ui.md).
    """

    outcome: Literal["approve", "reject"]


def _item_to_dict(item: ReviewItem) -> dict[str, Any]:
    """Serialize a review item for a front-end, surfacing the harness-gate fields (capability for a
    §6.2 permission, question/options for a §6.3 input) so a UI can render + resolve it."""
    kind = (
        "permission"
        if item.skill_id == "harness-approval"
        else "input"
        if item.skill_id == "harness-input"
        else "other"
    )
    return {
        "id": item.id,
        "kind": kind,
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
        "timestamp": item.timestamp.isoformat(),
    }


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

    @app.get("/review")
    async def list_pending() -> list[dict[str, Any]]:
        return [_item_to_dict(i) for i in _queue().list_pending()]

    @app.get("/review/all")
    async def list_all() -> list[dict[str, Any]]:
        return [_item_to_dict(i) for i in _queue().list_all()]

    @app.get("/review/{item_id}")
    async def get_item(item_id: str) -> dict[str, Any]:
        return _item_to_dict(_find(_queue(), item_id))

    @app.post("/review/{item_id}/approve")
    async def approve(item_id: str) -> dict[str, Any]:
        queue = _queue()
        item = _find(queue, item_id)
        queue.resolve(item.id, "approved")
        return _item_to_dict(_find(queue, item.id))

    @app.post("/review/{item_id}/reject")
    async def reject(item_id: str) -> dict[str, Any]:
        queue = _queue()
        item = _find(queue, item_id)
        queue.resolve(item.id, "rejected")
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
        queue.answer_input(item.id, resolved)
        return _item_to_dict(_find(queue, item.id))

    @app.post("/review/{item_id}/resolve")
    async def resolve_multiparty_task(
        item_id: str, body: ResolveRequest, request: Request
    ) -> dict[str, Any]:
        """Resolve a multi-party approval role-task as the authenticated caller.

        Authorize → check membership → record → audit, mirroring ``_ingress_pipeline_event``:
        ``approvals:resolve`` is a reserved human-identity scope (§8.7), so an agent or webhook
        token can never cast a resolution whatever its serve tier. Every attempt is audited,
        allowed or denied.
        """
        runtime = _get_runtime(request)
        identity = getattr(request.state, "identity", None)
        actor = getattr(identity, "client_id", None) or "anonymous"

        queue = _queue()
        item = _find(queue, item_id)
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
            # otherwise merely ignored by ``evaluate``, leaving the resolver watching a gate that
            # never advances. Under NoneAuthProvider this is what rejects "anonymous" — unless the
            # workspace genuinely lists it as a role member, which keeps local dev workable.
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
                    "outcome": body.outcome,
                    "identity": actor,
                    "allowed": denial is None,
                    "reason": denial or decision.reason,
                },
                policy_decision="allow" if denial is None else "deny",
                policy_reason=denial or decision.reason,
            )
        )

        if denial is not None:
            raise HTTPException(
                status_code=403,
                detail=f"{actor} may not resolve {item.id!r}: {denial}",
            )

        status: Literal["approved", "rejected"] = (
            "approved" if body.outcome == "approve" else "rejected"
        )
        queue.record_resolution(item.id, status, actor)
        return _item_to_dict(_find(queue, item.id))
