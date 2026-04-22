"""MockGovernanceProvider — deterministic, assertable, test-only.

See ``design/details/governance-provider-interface.md`` §MockGovernanceProvider.
"""

from __future__ import annotations

from . import (
    AgentCredential,
    AuditEvent,
    GovernanceProvider,
    IdentityVerification,
    PolicyDecision,
    TrustScore,
)

_DEFAULT_TRUST = 1.0
_DEFAULT_TIER = "fully-trusted"


class MockGovernanceProvider(GovernanceProvider):
    """Governance provider for unit tests.

    ``allowed_scopes`` controls which scopes pass ``evaluate_action``.
    ``trust_scores`` maps agent ids to float scores (default 1.0).
    Audit events are appended to an internal list accessible via ``.events``.
    """

    def __init__(
        self,
        *,
        allowed_scopes: frozenset[str] = frozenset(),
        trust_scores: dict[str, float] | None = None,
    ) -> None:
        self._allowed_scopes = allowed_scopes
        self._trust_scores = trust_scores or {}
        self._events: list[AuditEvent] = []

    # -- GovernanceProvider implementation --------------------------------

    async def evaluate_action(
        self,
        *,
        agent_id: str,
        action: str,
        scopes_required: frozenset[str],
        context: dict[str, object] | None = None,
    ) -> PolicyDecision:
        granted = scopes_required & self._allowed_scopes
        denied = scopes_required - self._allowed_scopes
        if denied:
            return PolicyDecision(
                allowed=False,
                reason=f"mock: agent '{agent_id}' denied scopes {sorted(denied)}",
                tier=1,
                scopes_granted=granted,
                scopes_denied=denied,
            )
        return PolicyDecision(
            allowed=True,
            reason=f"mock: agent '{agent_id}' granted all requested scopes",
            tier=1,
            scopes_granted=granted,
            scopes_denied=frozenset(),
        )

    async def verify_identity(
        self,
        *,
        agent_id: str,
        credential: AgentCredential,
    ) -> IdentityVerification:
        return IdentityVerification(verified=True, agent_id=agent_id)

    async def record_event(self, event: AuditEvent) -> None:
        self._events.append(event)

    async def get_trust_score(self, *, agent_id: str) -> TrustScore:
        score = self._trust_scores.get(agent_id, _DEFAULT_TRUST)
        tier = _DEFAULT_TIER if score >= 0.8 else "degraded"
        return TrustScore(score=score, tier=tier)

    # -- test helpers (read-only) -----------------------------------------

    @property
    def events(self) -> list[AuditEvent]:
        """Copy of recorded events. Intentionally no clear/delete method."""
        return list(self._events)
