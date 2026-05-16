"""SkillBackedGovernanceProvider — wraps a base provider with decision skill evaluation.

Delegates all standard governance methods to the base provider. Overrides
evaluate_decision_skill to actually invoke decision skills via the skill
executor + model provider.
"""

from __future__ import annotations

from typing import Any

from swarmkit_runtime.governance import (
    AgentCredential,
    AuditEvent,
    DecisionSkillResult,
    GovernanceProvider,
    IdentityVerification,
    PolicyDecision,
    TrustScore,
)
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol
from swarmkit_runtime.skills import ResolvedSkill


class SkillBackedGovernanceProvider(GovernanceProvider):
    """Wraps a base GovernanceProvider and adds decision skill evaluation.

    Standard governance calls (evaluate_action, verify_identity, etc.)
    pass through to the base provider. Decision skill evaluation uses
    the skill registry and model provider to actually invoke skills.
    """

    def __init__(
        self,
        *,
        base: GovernanceProvider,
        skills: dict[str, ResolvedSkill],
        model_provider: ModelProviderProtocol,
        model_name: str = "",
    ) -> None:
        self._base = base
        self._skills = skills
        self._model_provider = model_provider
        self._model_name = model_name

    async def evaluate_action(
        self,
        *,
        agent_id: str,
        action: str,
        scopes_required: frozenset[str],
        context: dict[str, object] | None = None,
    ) -> PolicyDecision:
        return await self._base.evaluate_action(
            agent_id=agent_id,
            action=action,
            scopes_required=scopes_required,
            context=context,
        )

    async def verify_identity(
        self,
        *,
        agent_id: str,
        credential: AgentCredential,
    ) -> IdentityVerification:
        return await self._base.verify_identity(
            agent_id=agent_id,
            credential=credential,
        )

    async def record_event(self, event: AuditEvent) -> None:
        await self._base.record_event(event)

    async def get_trust_score(self, *, agent_id: str) -> TrustScore:
        return await self._base.get_trust_score(agent_id=agent_id)

    async def evaluate_decision_skill(
        self,
        *,
        skill_id: str,
        trigger: str,
        agent_id: str,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> DecisionSkillResult:
        skill = self._skills.get(skill_id)
        if skill is None:
            return DecisionSkillResult(
                skill_id=skill_id,
                verdict="pass",
                confidence=0.0,
                reasoning=f"Decision skill '{skill_id}' not found in workspace.",
            )

        from swarmkit_runtime.governance._decision_evaluator import (  # noqa: PLC0415
            evaluate_skill,
        )

        return await evaluate_skill(
            skill=skill,
            agent_id=agent_id,
            trigger=trigger,
            content=content,
            model_provider=self._model_provider,
            model_name=self._model_name,
            context=context,
        )
