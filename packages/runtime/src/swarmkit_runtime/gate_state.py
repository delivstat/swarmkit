"""Gate state — is this gate resolved, with the approval policy applied (read-only).

``design/details/gate-state-and-deferring-approval.md``, Part 1.

``GET /review?gate_id=…`` returns the individual role-tasks. Turning those into a decision means
applying quorum, distinct-approver counts and ``exclude_author`` — which lives in
:func:`collect_resolutions` and :func:`evaluate`, and is exactly the SwarmKit-shaped part a caller
must not rebuild. It is also the one thing a caller cannot derive for itself: the policy lives in a
funnel, which the caller does not read.

Without this, an application sequencing its own runs either reimplements the approval policy or
approximates it, and an approximation of an approval policy is a governance failure with a friendly
name.

**The policy is resolved from the items, not from the gate id.** ``open_gate`` stamps ``funnel_id``
on every role-task it writes, for precisely this purpose. That matters because two gate-id shapes
exist — ``{topology_id}:{agent_id}`` in-node and ``{correlation_id}:{agent_id}`` for a stage — and a
resolver that parsed the id would have to know which, and be wrong for the other. Reading the funnel
off the item works for both, and keeps working when the shapes are unified.

Both ``GET /gates/{gate_id}`` and ``swarmkit review gate`` call :func:`compute_gate_state` — one
pure function, surfaced twice, as ``gate_coverage`` is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from swarmkit_runtime.governance._approval import ApprovalPolicy, evaluate, tasks
from swarmkit_runtime.review._multiparty import collect_resolutions

if TYPE_CHECKING:
    from swarmkit_runtime.governance._approval import RoleRegistry
    from swarmkit_runtime.resolver._resolved import ResolvedWorkspace
    from swarmkit_runtime.review import ReviewItem, ReviewQueue


class UnknownGateError(KeyError):
    """No role-task items exist for this gate id."""


class PolicyUnresolvedError(LookupError):
    """The gate's items exist but its funnel — and so its policy — cannot be read."""


@dataclass(frozen=True)
class Resolution:
    """One role-task's recorded decision, as a reader needs to see it."""

    item_id: str
    role: str
    scope: str
    status: str
    resolved_by: str
    comment: str
    stale: bool


@dataclass(frozen=True)
class GateState:
    """A gate's live state with its policy applied.

    ``status`` is the only field a driver must understand; the rest is for a human reading why.
    """

    gate_id: str
    status: str  # pending | approved | rejected | changes-requested
    funnel_id: str
    topology_id: str
    agent_id: str
    artifact_ref: str
    round: int
    resolutions: tuple[Resolution, ...]
    distinct_approvers: tuple[str, ...]
    outstanding: tuple[str, ...]
    #: True when the policy excludes the author but no author identity was supplied, so that rule
    #: could not be applied. Stated rather than silently skipped: without it a caller could read
    #: `approved` for a gate whose real evaluation would discount the author's own approval, and an
    #: approval that is wrong in the permissive direction is the worst kind.
    exclude_author_unapplied: bool = False

    @property
    def resolved(self) -> bool:
        """Whether the gate has reached a terminal state — what a poller is waiting for."""
        return self.status in {"approved", "rejected"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status,
            "resolved": self.resolved,
            "funnel_id": self.funnel_id,
            "topology_id": self.topology_id,
            "agent_id": self.agent_id,
            "artifact_ref": self.artifact_ref,
            "round": self.round,
            "distinct_approvers": list(self.distinct_approvers),
            "outstanding": list(self.outstanding),
            "exclude_author_unapplied": self.exclude_author_unapplied,
            "resolutions": [
                {
                    "item_id": r.item_id,
                    "role": r.role,
                    "scope": r.scope,
                    "status": r.status,
                    "resolved_by": r.resolved_by,
                    "comment": r.comment,
                    "stale": r.stale,
                }
                for r in self.resolutions
            ],
        }


def _items_for(queue: ReviewQueue, gate_id: str) -> list[ReviewItem]:
    return [i for i in queue.list_all() if (i.output or {}).get("gate_id") == gate_id]


def _policy_for(workspace: ResolvedWorkspace, funnel_id: str) -> ApprovalPolicy:
    funnel = workspace.funnels.get(funnel_id) if funnel_id else None
    approve = (dict(funnel.spec).get("approve") if funnel is not None else None) or None
    if approve is None:
        raise PolicyUnresolvedError(
            f"funnel {funnel_id!r} is not in this workspace, or declares no `approve` layer, "
            f"so the gate's policy cannot be applied"
        )
    return ApprovalPolicy.from_dict(approve)


def compute_gate_state(
    queue: ReviewQueue,
    registry: RoleRegistry,
    workspace: ResolvedWorkspace,
    gate_id: str,
    *,
    author: str | None = None,
) -> GateState:
    """Read a gate's role-tasks and apply its funnel's approval policy to them.

    Raises :class:`UnknownGateError` when no items carry this gate id, and
    :class:`PolicyUnresolvedError` when they do but the funnel cannot be read — never a guess. A
    gate whose policy is unknown is not the same as a gate that is pending, and reporting one as the
    other is how an approval gets skipped.
    """
    items = _items_for(queue, gate_id)
    if not items:
        raise UnknownGateError(gate_id)

    first = items[0]
    funnel_id = str((first.output or {}).get("funnel_id") or "")
    policy = _policy_for(workspace, funnel_id)

    # The artifact under review is the newest round's; earlier rounds decided a different artifact.
    current_round = max(getattr(i, "round", 0) for i in items)
    current = [i for i in items if getattr(i, "round", 0) == current_round]
    artifact_ref = next((i.artifact_ref for i in current if i.artifact_ref), "")

    collected = collect_resolutions(
        queue, gate_id=gate_id, policy=policy, artifact_ref=artifact_ref
    )
    ev = evaluate(policy, registry, collected, author)

    unapplied = bool(getattr(policy, "exclude_author", False)) and author is None

    return GateState(
        gate_id=gate_id,
        status=str(ev.status.value),
        funnel_id=funnel_id,
        topology_id=first.topology_id,
        agent_id=first.agent_id,
        artifact_ref=artifact_ref,
        round=current_round,
        resolutions=tuple(
            Resolution(
                item_id=i.id,
                role=str((i.output or {}).get("role", "")),
                scope=str((i.output or {}).get("scope", "")),
                status=i.status,
                resolved_by=i.resolved_by,
                comment=i.comment,
                # A decision from an earlier round was made about a different artifact: still shown,
                # deliberately not counted (`collect_resolutions`).
                stale=getattr(i, "round", 0) != current_round,
            )
            for i in sorted(items, key=lambda x: x.id)
        ),
        distinct_approvers=tuple(sorted(ev.distinct_approvers)),
        outstanding=tuple(f"{t.role} ({t.scope})" for t in ev.outstanding),
        exclude_author_unapplied=unapplied,
    )


def gate_ids(queue: ReviewQueue) -> list[str]:
    """Every gate id present in the queue, newest-agnostic and sorted — for listing surfaces."""
    seen = {
        str((i.output or {}).get("gate_id") or "")
        for i in queue.list_all()
        if (i.output or {}).get("gate_id")
    }
    return sorted(seen)


__all__ = [
    "GateState",
    "PolicyUnresolvedError",
    "Resolution",
    "UnknownGateError",
    "compute_gate_state",
    "gate_ids",
    "tasks",
]
