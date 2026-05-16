"""MockGovernanceProvider — deterministic, assertable, test-only.

See ``design/details/governance-provider-interface.md`` §MockGovernanceProvider.
"""

from __future__ import annotations

from typing import Any

from . import (
    AgentCredential,
    AuditEvent,
    DecisionSkillResult,
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
        allow_all: bool = False,
        trust_scores: dict[str, float] | None = None,
        decision_skill_verdicts: dict[str, DecisionSkillResult] | None = None,
    ) -> None:
        self._allowed_scopes = allowed_scopes
        self._allow_all = allow_all
        self._trust_scores = trust_scores or {}
        self._decision_skill_verdicts = decision_skill_verdicts or {}
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
        tier_decision = self._check_permission_tier(agent_id, action, context)
        if tier_decision is not None:
            return tier_decision

        if self._allow_all:
            return PolicyDecision(
                allowed=True,
                reason=f"mock: agent '{agent_id}' allowed (no enforcement)",
                tier=1,
                scopes_granted=scopes_required,
                scopes_denied=frozenset(),
            )
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

    @staticmethod
    def _check_permission_tier(
        agent_id: str,
        action: str,
        context: dict[str, object] | None,
    ) -> PolicyDecision | None:
        """Apply MCP server permission tier rules from context.

        Returns a PolicyDecision if the tier forces a decision (readonly
        denying writes, strict denying unless explicitly approved), or
        None to fall through to normal scope-based evaluation.
        """
        if context is None:
            return None
        tier = context.get("server_permission")
        if tier is None:
            return None

        if tier == "readonly":
            _write_signals = (
                "create",
                "delete",
                "update",
                "write",
                "put",
                "post",
                "set",
                "add",
                "remove",
                "modify",
                "edit",
                "insert",
                "drop",
                "push",
                "send",
            )
            action_lower = action.lower()
            if any(sig in action_lower for sig in _write_signals):
                return PolicyDecision(
                    allowed=False,
                    reason=f"mock: server permission 'readonly' denies write action '{action}'",
                    tier=1,
                )

        if tier == "strict":
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"mock: server permission 'strict' requires explicit approval "
                    f"for agent '{agent_id}' action '{action}'"
                ),
                tier=1,
            )

        return None

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

    async def evaluate_decision_skill(
        self,
        *,
        skill_id: str,
        trigger: str,
        agent_id: str,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> DecisionSkillResult:
        if self._decision_skill_verdicts and skill_id in self._decision_skill_verdicts:
            return self._decision_skill_verdicts[skill_id]
        return DecisionSkillResult(
            skill_id=skill_id,
            verdict="pass",
            confidence=1.0,
            reasoning=f"mock: decision skill '{skill_id}' auto-passed",
        )

    # -- test helpers (read-only) -----------------------------------------

    @property
    def events(self) -> list[AuditEvent]:
        """Copy of recorded events. Intentionally no clear/delete method."""
        return list(self._events)
