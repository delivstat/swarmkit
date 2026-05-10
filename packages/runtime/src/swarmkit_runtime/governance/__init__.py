"""GovernanceProvider abstraction (design §8.5).

The runtime interacts with governance only through this interface. AGT is the
v1.0 implementation; future implementations replace it without changes to the
topology schema, runtime, or user experience.

See ``design/details/governance-provider-interface.md`` for the finalised
method signatures, type contracts, and async rationale.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from swarmkit_runtime.governance._limits import (
    CircuitBreakerError,
    CircuitBreakerTracker,
    GovernanceLimits,
)

# ---- types --------------------------------------------------------------


@dataclass(frozen=True)
class PolicyDecision:
    """Result of a governance policy evaluation (§8.6 tiered model)."""

    allowed: bool
    reason: str
    tier: int  # 1 (deterministic), 2 (single LLM judge), 3 (panel)
    scopes_granted: frozenset[str] = field(default_factory=frozenset)
    scopes_denied: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class AuditEvent:
    """Append-only event emitted through the media pillar (§8.3).

    Expanded in M6 to carry full structured observability fields.
    All new fields are optional for backward compatibility — existing
    event construction sites continue to work without changes.
    """

    # Required fields (existing)
    event_type: str
    agent_id: str
    timestamp: datetime
    payload: dict[str, object] = field(default_factory=dict)
    topology_id: str | None = None
    skill_id: str | None = None

    # M6 expansion: identity + correlation
    event_id: UUID = field(default_factory=uuid4)
    run_id: str | None = None
    parent_event_id: UUID | None = None

    # M6 expansion: agent context
    agent_role: Literal["root", "leader", "worker"] | None = None
    skill_category: Literal["capability", "decision", "coordination", "persistence"] | None = None

    # M6 expansion: I/O (redactable)
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None

    # M6 expansion: decision skills
    verdict: Literal["pass", "fail", "needs-review"] | None = None
    reasoning: str | None = None
    confidence: float | None = None

    # M6 expansion: model usage
    model_provider: str | None = None
    model_name: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None

    # M6 expansion: timing + governance
    duration_ms: int | None = None
    policy_decision: Literal["allow", "deny"] | None = None
    policy_reason: str | None = None

    # M6 expansion: error
    error: dict[str, str] | None = None


# ---- redaction utility ---------------------------------------------------

_SUMMARY_MAX_BYTES = 200


def redact_json_pointers(obj: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    """Redact fields specified by JSON pointer-like paths (e.g. '$.password').

    Returns a shallow copy with redacted fields replaced by '[REDACTED]'.
    Supports top-level keys only for now (no deep nested traversal).
    """
    if not paths or not obj:
        return dict(obj)
    result = dict(obj)
    for path in paths:
        key = re.sub(r"^\$\.?", "", path)
        if key in result:
            result[key] = "[REDACTED]"
    return result


def summarize_value(value: Any) -> str:
    """Produce a summary of a value (first 200 bytes + type info)."""
    text = str(value)
    if len(text) <= _SUMMARY_MAX_BYTES:
        return text
    return text[:_SUMMARY_MAX_BYTES] + f"... ({type(value).__name__}, {len(text)} chars)"


# ---- workspace-scoped event factories ------------------------------------


def run_started_event(
    *,
    run_id: str,
    topology_id: str,
    trigger_source: str = "cli",
    inputs: dict[str, Any] | None = None,
) -> AuditEvent:
    """Create a run_started event."""
    return AuditEvent(
        event_type="run.started",
        agent_id="runtime",
        timestamp=datetime.now(tz=datetime.now().astimezone().tzinfo),
        run_id=run_id,
        topology_id=topology_id,
        payload={"trigger_source": trigger_source},
        inputs=inputs,
    )


def run_ended_event(
    *,
    run_id: str,
    topology_id: str,
    status: Literal["success", "error", "aborted"],
    duration_ms: int,
    total_cost_usd: float | None = None,
    error: dict[str, str] | None = None,
) -> AuditEvent:
    """Create a run_ended event."""
    return AuditEvent(
        event_type="run.ended",
        agent_id="runtime",
        timestamp=datetime.now(tz=datetime.now().astimezone().tzinfo),
        run_id=run_id,
        topology_id=topology_id,
        duration_ms=duration_ms,
        cost_usd=total_cost_usd,
        payload={"status": status},
        error=error,
    )


def hitl_requested_event(
    *,
    run_id: str,
    agent_id: str,
    review_queue_id: str,
    summary: str,
) -> AuditEvent:
    """Create a hitl_requested event."""
    return AuditEvent(
        event_type="hitl.requested",
        agent_id=agent_id,
        timestamp=datetime.now(tz=datetime.now().astimezone().tzinfo),
        run_id=run_id,
        payload={"review_queue_id": review_queue_id, "summary": summary},
    )


def hitl_resolved_event(
    *,
    run_id: str,
    agent_id: str,
    decision: Literal["approved", "rejected"],
    by_user: str | None = None,
) -> AuditEvent:
    """Create a hitl_resolved event."""
    return AuditEvent(
        event_type="hitl.resolved",
        agent_id=agent_id,
        timestamp=datetime.now(tz=datetime.now().astimezone().tzinfo),
        run_id=run_id,
        payload={"decision": decision, "by_user": by_user or "unknown"},
    )


@dataclass(frozen=True)
class AgentCredential:
    """Opaque credential presented for identity verification (§16.1)."""

    credential_type: str  # "ed25519", "did", "mock"
    value: str


@dataclass(frozen=True)
class IdentityVerification:
    """Result of an identity verification request."""

    verified: bool
    agent_id: str


@dataclass(frozen=True)
class TrustScore:
    """Behavioural trust score for an agent (§16.1, 0.0-1.0)."""

    score: float
    tier: str


# ---- ABC ----------------------------------------------------------------


class GovernanceProvider(ABC):
    """Narrow, stable interface over the governance toolkit.

    See design §8.5 for rationale — this is the abstraction that keeps
    SwarmKit portable across governance implementations. All methods are
    async (governance calls may involve I/O) and keyword-only past
    ``self`` (prevents positional mix-ups as signatures evolve).
    """

    @abstractmethod
    async def evaluate_action(
        self,
        *,
        agent_id: str,
        action: str,
        scopes_required: frozenset[str],
        context: dict[str, object] | None = None,
    ) -> PolicyDecision:
        """Ask the governance layer whether this action is allowed."""

    @abstractmethod
    async def verify_identity(
        self,
        *,
        agent_id: str,
        credential: AgentCredential,
    ) -> IdentityVerification:
        """Verify the agent's identity credential."""

    @abstractmethod
    async def record_event(self, event: AuditEvent) -> None:
        """Append an event to the audit log (append-only; no update/delete)."""

    @abstractmethod
    async def get_trust_score(self, *, agent_id: str) -> TrustScore:
        """Return the current trust score for the agent."""


__all__ = [
    "AgentCredential",
    "AuditEvent",
    "CircuitBreakerError",
    "CircuitBreakerTracker",
    "GovernanceLimits",
    "GovernanceProvider",
    "IdentityVerification",
    "PolicyDecision",
    "TrustScore",
    "hitl_requested_event",
    "hitl_resolved_event",
    "redact_json_pointers",
    "run_ended_event",
    "run_started_event",
    "summarize_value",
]
