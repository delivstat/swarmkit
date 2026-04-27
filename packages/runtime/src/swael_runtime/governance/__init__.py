"""GovernanceProvider abstraction (design §8.5).

The runtime interacts with governance only through this interface. AGT is the
v1.0 implementation; future implementations replace it without changes to the
topology schema, runtime, or user experience.

See ``design/details/governance-provider-interface.md`` for the finalised
method signatures, type contracts, and async rationale.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

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
    """Append-only event emitted through the media pillar (§8.3)."""

    event_type: str
    agent_id: str
    timestamp: datetime
    payload: dict[str, object] = field(default_factory=dict)
    topology_id: str | None = None
    skill_id: str | None = None


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
    "GovernanceProvider",
    "IdentityVerification",
    "PolicyDecision",
    "TrustScore",
]
