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


@dataclass(frozen=True)
class MultiPartyDecision:
    status: GateStatus
    approvers: frozenset[str]
    reason: str = ""

    @property
    def approved(self) -> bool:
        return self.status is GateStatus.APPROVED


def role_task_item_id(gate_id: str, rule_index: int, role: str) -> str:
    """Deterministic id for the review item of one role-task in a gate."""
    return f"mpa-{gate_id}-{rule_index}-{role}"


def open_gate(
    queue: ReviewQueue,
    *,
    gate_id: str,
    topology_id: str,
    agent_id: str,
    policy: ApprovalPolicy,
) -> None:
    """Fan the gate out into one review item per role-task.

    Idempotent: an already-submitted item (e.g. one already resolved, on a resume or a re-open) is
    left untouched, so re-opening a gate never clobbers collected approvals.
    """
    for task in tasks(policy):
        item_id = role_task_item_id(gate_id, task.rule_index, task.role)
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
                },
                verdict={},
                reason=f"role {task.role!r} must approve {task.scope!r}",
                timestamp=datetime.now(tz=UTC),
            )
        )


def collect_resolutions(
    queue: ReviewQueue, *, gate_id: str, policy: ApprovalPolicy
) -> list[Resolution]:
    """Read the resolved role-task items for a gate into engine resolutions.

    The resolver identity is the item's ``answer``; status maps approved -> approve,
    rejected -> reject. Pending items contribute nothing.
    """
    out: list[Resolution] = []
    for task in tasks(policy):
        item = queue.get(role_task_item_id(gate_id, task.rule_index, task.role))
        if item is None or item.status == "pending":
            continue
        outcome: Literal["approve", "reject"] = (
            "approve" if item.status == "approved" else "reject"
        )
        out.append(
            Resolution(
                identity=item.answer,
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
