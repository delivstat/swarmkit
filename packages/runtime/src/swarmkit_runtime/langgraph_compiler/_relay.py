"""The relay orchestrator — generic, driver-agnostic core (executor-relay-plan.md, RFC §6.2).

When a harness under ``on_unanswerable: relay`` surfaces a mid-run permission request, this resolves
a decision: **policy first** (an auto-approve from the trust allowlist — no human), else the request
enters the approval **inbox** and a **bounded wait** for an operator. On timeout it degrades to a
denial so the run can ``abort`` — the never-hang guarantee holds. Every request/response is audited
with responder identity, and an approval is scoped to the single action (it never widens the grant).

Feeding the decision back into the live session is the driver's job (`_interaction.py`); this module
decides *what* the answer is, vendor-neutrally.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from swarmkit_runtime.executors import ExecApprovalRequested, ExecInputRequested
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.review import ReviewItem

if TYPE_CHECKING:
    from swarmkit_runtime.governance import GovernanceProvider
    from swarmkit_runtime.review import ReviewQueue
    from swarmkit_runtime.trust import TrustStore

_DEFAULT_MAX_WAIT_SECONDS = 300.0


@dataclass(frozen=True)
class RelayDecision:
    """The resolved outcome of a relayed permission request."""

    granted: bool
    responder: str  # "policy" | "operator" | "timeout"
    reason: str = ""

    @property
    def timed_out(self) -> bool:
        return self.responder == "timeout"


async def resolve_relay(
    request: ExecApprovalRequested,
    *,
    agent_id: str,
    topology_id: str,
    governance: GovernanceProvider,
    review_queue: ReviewQueue,
    max_wait_seconds: float | None = None,
    trust: TrustStore | None = None,
    archetype: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    poll_interval: float = 0.5,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> RelayDecision:
    """Resolve a relayed permission request: policy auto-approve → inbox → bounded wait → abort.

    ``clock`` / ``sleep`` are injectable so the wait is testable without real time. When ``trust`` +
    ``archetype`` are given, each **operator** decision is folded into the trust tally (§6.2.3): N
    approvals ⇒ an allowlist-changeset proposal; one denial resets + blocks. Policy auto-approvals
    (already allowlisted) and timeouts (not a deliberate answer) are not accrued.
    """
    capability = request.capability
    await _audit(
        governance,
        "executor.approval_requested",
        agent_id,
        {"capability": capability, "rationale": request.rationale},
    )

    # 1. Policy first: the trust allowlist may already permit this (archetype, capability) pair.
    decision = await governance.evaluate_action(
        agent_id=agent_id,
        action=f"harness:approve:{capability}",
        scopes_required=frozenset(),
        context={"capability": capability, "rationale": request.rationale or ""},
    )
    if decision.allowed:
        return await _finish(
            governance, agent_id, RelayDecision(True, "policy", decision.reason), capability
        )

    # 2. Inbox gate — a human decision, scoped to this single action.
    item = ReviewItem(
        id=f"relay-{request.run_id}-{capability}",
        topology_id=topology_id,
        agent_id=agent_id,
        skill_id="harness-approval",
        output={"capability": capability, "rationale": request.rationale or ""},
        verdict={},
        reason=f"harness requests permission for {capability!r}",
        timestamp=datetime.now(tz=UTC),
    )
    review_queue.submit(item)

    # 3. Bounded wait — degrade to a denial on timeout so the run can abort (never hang).
    budget = _DEFAULT_MAX_WAIT_SECONDS if max_wait_seconds is None else max_wait_seconds
    start = clock()
    while clock() - start < budget:
        current = review_queue.get(item.id)
        if current is not None and current.status == "approved":
            dec = await _finish(governance, agent_id, RelayDecision(True, "operator"), capability)
            await _accrue(governance, agent_id, trust, archetype, capability, dec)
            return dec
        if current is not None and current.status == "rejected":
            dec = await _finish(governance, agent_id, RelayDecision(False, "operator"), capability)
            await _accrue(governance, agent_id, trust, archetype, capability, dec)
            return dec
        await sleep(poll_interval)
    return await _finish(
        governance, agent_id, RelayDecision(False, "timeout", "approval wait expired"), capability
    )


async def _finish(
    governance: GovernanceProvider, agent_id: str, decision: RelayDecision, capability: str
) -> RelayDecision:
    await _audit(
        governance,
        "executor.approval_response",
        agent_id,
        {
            "capability": capability,
            "granted": decision.granted,
            "responder": decision.responder,
            "scope": "this-action-only",
        },
    )
    return decision


async def _accrue(
    governance: GovernanceProvider,
    agent_id: str,
    trust: TrustStore | None,
    archetype: str | None,
    capability: str,
    decision: RelayDecision,
) -> None:
    """Fold one operator decision into the trust tally; audit a proposal the run it is first made.
    The store only *proposes* — the grant is widened later by a human (``swarmkit trust apply``)."""
    if trust is None or not archetype:
        return
    proposal = trust.record(archetype, capability, decision.granted)
    if proposal is not None:
        await _audit(
            governance,
            "trust.changeset_proposed",
            agent_id,
            {
                "archetype": archetype,
                "capability": capability,
                "approvals": proposal.approvals,
            },
        )


async def resolve_input(
    request: ExecInputRequested,
    *,
    agent_id: str,
    topology_id: str,
    governance: GovernanceProvider,
    review_queue: ReviewQueue,
    max_wait_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    poll_interval: float = 0.5,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> str | None:
    """Resolve a §6.3 input request via the human inbox: submit the question + options, wait for a
    textual answer, and return it. Returns ``None`` on timeout or rejection (the run then aborts —
    never hangs). Every request/response is audited. (Lead-node auto-answer is deferred; a human
    answers for now.)"""
    await _audit(
        governance,
        "executor.input_requested",
        agent_id,
        {"question": request.question, "options": list(request.options)},
    )
    item = ReviewItem(
        id=f"input-{agent_id}-{abs(hash(request.question)) % 10_000_000}",
        topology_id=topology_id,
        agent_id=agent_id,
        skill_id="harness-input",
        output={
            "question": request.question,
            "options": list(request.options),
            "free_text_allowed": request.free_text_allowed,
        },
        verdict={},
        reason=f"harness needs input: {request.question}",
        timestamp=datetime.now(tz=UTC),
    )
    review_queue.submit(item)

    budget = _DEFAULT_MAX_WAIT_SECONDS if max_wait_seconds is None else max_wait_seconds
    start = clock()
    answer: str | None = None
    responder = "timeout"
    while clock() - start < budget:
        current = review_queue.get(item.id)
        if current is not None and current.status == "approved":
            answer, responder = current.answer, "operator"
            break
        if current is not None and current.status == "rejected":
            responder = "operator"
            break
        await sleep(poll_interval)

    await _audit(
        governance,
        "executor.input_response",
        agent_id,
        {"question": request.question, "answer": answer or "", "responder": responder},
    )
    return answer


async def _audit(
    governance: GovernanceProvider, event_type: str, agent_id: str, payload: dict[str, object]
) -> None:
    await governance.record_event(
        AuditEvent(
            event_type=event_type,
            agent_id=agent_id,
            timestamp=datetime.now(tz=UTC),
            payload=payload,
        )
    )
