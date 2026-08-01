"""Multi-party approval gate resolution (design/details/multi-party-approval.md).

Wires the pure approval engine (``governance._approval``) into the runtime review
queue. Mirrors ``langgraph_compiler._relay.resolve_relay``: a gate fans out into one
review item per role-task, then a bounded poll collects resolutions and drives them
through ``evaluate`` until the gate is APPROVED / REJECTED, degrading to a denial on
timeout so a run never hangs.

Enforcement is structural: only resolutions from a registry member of the required
role, for a scope that role confers, count (``resolution_error``) — an agent identity
is not a registry member, so it can never satisfy a human-reserved approval scope.
Every resolution + the gate open/close is an append-only audit event.

The resolver identity is carried on each item's ``answer`` field, set when a human
resolves the item (approve or reject) through serve ``/review`` or the CLI.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from swarmkit_runtime.governance import AuditEvent, GovernanceProvider
from swarmkit_runtime.governance._approval import (
    ApprovalPolicy,
    GateStatus,
    Resolution,
    RoleRegistry,
    evaluate,
    tasks,
)
from swarmkit_runtime.review import ReviewItem, ReviewQueue

_DEFAULT_MAX_WAIT_SECONDS = 7 * 24 * 3600.0  # a gate may legitimately wait a long time

#: Bound on rework loops for one gate. A non-deterministic artifact (or a store that mints a fresh
#: ref for identical content) would otherwise re-ask the roles forever and the gate would never
#: close; past this the caller escalates instead of looping.
_MAX_GATE_ROUNDS = 20


@dataclass(frozen=True)
class MultiPartyDecision:
    status: GateStatus
    approvers: frozenset[str]
    reason: str = ""

    @property
    def approved(self) -> bool:
        return self.status is GateStatus.APPROVED


def role_task_item_id(gate_id: str, rule_index: int, role: str, round_no: int = 0) -> str:
    """Deterministic id for the review item of one role-task in one round of a gate.

    Round 0 keeps the historical id, so items written before rounds existed still resolve.
    """
    base = f"mpa-{gate_id}-{rule_index}-{role}"
    return base if round_no == 0 else f"{base}-r{round_no}"


def _current_round(
    queue: ReviewQueue, gate_id: str, policy: ApprovalPolicy, artifact_ref: str
) -> int:
    """The round this gate is on for *artifact_ref*.

    Reuses the round already opened against the same artifact (so a restart or duplicate signal is
    free), otherwise the next one. An empty ref — an externally-driven gate, or a caller that does
    not track artifacts — always maps to round 0, preserving today's behaviour exactly.
    """
    if not artifact_ref:
        return 0
    highest = -1
    for round_no in range(_MAX_GATE_ROUNDS):
        item = queue.get(
            role_task_item_id(gate_id, tasks(policy)[0].rule_index, tasks(policy)[0].role, round_no)
        )
        if item is None:
            break
        highest = round_no
        if item.artifact_ref == artifact_ref:
            return round_no
    return highest + 1


def membership_error(registry: RoleRegistry, *, role: str, scope: str, identity: str) -> str | None:
    """Return why *identity* may not resolve this role-task, or None if it may.

    The registry-only subset of :func:`governance._approval.resolution_error`, for callers that
    hold a role-task item but not the gate's :class:`ApprovalPolicy` — the (scope, role) pairing is
    already guaranteed for an item ``open_gate`` created from that policy, so only the role's
    existence, its conferral of the scope, and membership remain to check.

    Used to fail a resolution *closed at the surface* with a specific reason. Without it an
    ineligible resolution is merely dropped by ``evaluate``, leaving the resolver looking at a gate
    that silently never advances. ``exclude_author`` still lives in the engine — it needs the
    policy — so this is a necessary, not a sufficient, check.
    """
    if registry.get(role) is None:
        return f"unknown role: {role}"
    if not registry.confers(role, scope):
        return f"role {role} does not confer scope {scope}"
    if not registry.is_member(role, identity):
        return f"{identity} is not a member of role {role}"
    return None


def open_gate(
    queue: ReviewQueue,
    *,
    gate_id: str,
    topology_id: str,
    agent_id: str,
    policy: ApprovalPolicy,
    funnel_id: str = "",
    artifact_ref: str = "",
) -> int:
    """Fan the gate out into one review item per role-task. Returns the gate's current round.

    Idempotent on the SAME artifact: re-opening after a restart or a duplicate signal leaves items
    untouched, so collected approvals are never clobbered.

    On a DIFFERENT artifact — a rework loop re-ran the stage and produced a new one — the round
    advances and fresh role-tasks are opened for it. Prior decisions are retained on their items,
    still returned by the read APIs and still rendered, but they were made about a different
    artifact and no longer count toward quorum
    (design/details/human-decision-comments.md, "Decided: retain and re-ask").
    """
    round_no = _current_round(queue, gate_id, policy, artifact_ref)
    for task in tasks(policy):
        item_id = role_task_item_id(gate_id, task.rule_index, task.role, round_no)
        if queue.get(item_id) is not None:
            continue
        queue.submit(
            ReviewItem(
                id=item_id,
                topology_id=topology_id,
                agent_id=agent_id,
                skill_id="multi-party-approval",
                output={
                    "gate_id": gate_id,
                    "scope": task.scope,
                    "role": task.role,
                    "rule_index": task.rule_index,
                    # The funnel this gate came from. Carried on the item so a resolver can rebuild
                    # the policy to re-evaluate the gate, without walking saga -> graph -> stage.
                    "funnel_id": funnel_id,
                },
                artifact_ref=artifact_ref,
                round=round_no,
                verdict={},
                reason=f"role {task.role!r} must approve {task.scope!r}",
                timestamp=datetime.now(tz=UTC),
            )
        )
    return round_no


_STATUS_TO_OUTCOME: dict[str, Literal["approve", "changes-requested", "reject"]] = {
    "approved": "approve",
    "rejected": "reject",
    "changes-requested": "changes-requested",
}


def collect_resolutions(
    queue: ReviewQueue, *, gate_id: str, policy: ApprovalPolicy, artifact_ref: str = ""
) -> list[Resolution]:
    """Read the resolved role-task items for a gate into engine resolutions.

    Only decisions made about **this** artifact are yielded. A decision from an earlier round was
    made about a different artifact — the reviewer has not seen the revision that answered them —
    so it stays on its item, stays visible to the read APIs and stays rendered as stale, but does
    not count toward quorum (design/details/human-decision-comments.md).

    An empty *artifact_ref* means "do not filter", which is how an externally-driven gate and every
    item written before rounds existed keep behaving exactly as before.
    """
    out: list[Resolution] = []
    round_no = _current_round(queue, gate_id, policy, artifact_ref) if artifact_ref else 0
    for task in tasks(policy):
        item = queue.get(role_task_item_id(gate_id, task.rule_index, task.role, round_no))
        if item is None or item.status == "pending":
            continue
        if artifact_ref and item.artifact_ref and item.artifact_ref != artifact_ref:
            continue
        outcome = _STATUS_TO_OUTCOME.get(item.status)
        if outcome is None:
            continue
        out.append(
            Resolution(
                identity=item.resolved_by or item.answer,
                role=task.role,
                scope=task.scope,
                outcome=outcome,
            )
        )
    return out


async def _audit(
    governance: GovernanceProvider, event_type: str, agent_id: str, payload: dict[str, Any]
) -> None:
    await governance.record_event(
        AuditEvent(
            event_type=event_type,
            agent_id=agent_id,
            timestamp=datetime.now(tz=UTC),
            payload=payload,
        )
    )


async def resolve_multiparty(
    *,
    gate_id: str,
    policy: ApprovalPolicy,
    registry: RoleRegistry,
    topology_id: str,
    agent_id: str,
    governance: GovernanceProvider,
    review_queue: ReviewQueue,
    author: str | None = None,
    max_wait_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    poll_interval: float = 0.5,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> MultiPartyDecision:
    """Open a multi-party gate and wait (bounded) until it is APPROVED / REJECTED.

    ``clock`` / ``sleep`` are injectable so the wait is testable without real time.
    On timeout the gate degrades to a denial — a run never hangs.
    """
    open_gate(
        review_queue, gate_id=gate_id, topology_id=topology_id, agent_id=agent_id, policy=policy
    )
    await _audit(
        governance,
        "approval.gate_opened",
        agent_id,
        {"gate_id": gate_id, "role_tasks": len(tasks(policy))},
    )

    budget = _DEFAULT_MAX_WAIT_SECONDS if max_wait_seconds is None else max_wait_seconds
    start = clock()
    while clock() - start < budget:
        resolutions = collect_resolutions(review_queue, gate_id=gate_id, policy=policy)
        ev = evaluate(policy, registry, resolutions, author)
        if ev.status in (GateStatus.APPROVED, GateStatus.REJECTED):
            await _audit(
                governance,
                "approval.gate_resolved",
                agent_id,
                {
                    "gate_id": gate_id,
                    "status": ev.status.value,
                    "approvers": sorted(ev.distinct_approvers),
                },
            )
            return MultiPartyDecision(ev.status, ev.distinct_approvers)
        await sleep(poll_interval)

    await _audit(
        governance,
        "approval.gate_resolved",
        agent_id,
        {"gate_id": gate_id, "status": "timeout"},
    )
    return MultiPartyDecision(GateStatus.REJECTED, frozenset(), "approval wait expired")
