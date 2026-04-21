"""GovernanceProvider abstraction (design §8.5).

The runtime interacts with governance only through this interface. AGT is the
v1.0 implementation; future implementations replace it without changes to the
topology schema, runtime, or user experience.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class Credential:
    agent_id: str
    signature: bytes
    issued_at: float


@dataclass(frozen=True)
class AuditEvent:
    agent_id: str
    event_type: str
    payload: dict[str, Any]
    timestamp: float


class GovernanceProvider(ABC):
    """Narrow, stable interface over the governance toolkit.

    See design §8.5 for rationale — this is the abstraction that keeps SwarmKit
    portable across governance implementations. Everything above this interface
    (topology schema, runtime, skills, archetypes, UI) stays unchanged when the
    underlying toolkit is swapped.
    """

    @abstractmethod
    def evaluate_action(
        self, agent_id: str, action: str, context: dict[str, Any]
    ) -> PolicyDecision:
        """Ask the governance layer whether this action is allowed."""

    @abstractmethod
    def verify_identity(self, agent_id: str, credential: Credential) -> bool:
        """Verify the agent's identity."""

    @abstractmethod
    def record_event(self, event: AuditEvent) -> None:
        """Append an event to the audit log (append-only; no update/delete)."""

    @abstractmethod
    def get_trust_score(self, agent_id: str) -> float:
        """Return the current trust score for the agent (0.0-1.0 normalised)."""


__all__ = [
    "AuditEvent",
    "Credential",
    "GovernanceProvider",
    "PolicyDecision",
]
