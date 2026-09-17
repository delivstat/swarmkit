"""The permission seam for an ``agent`` skill — sibling of ``check_mcp_permission``.

There is no server or pack to inherit a tier from, so the skill's own ``permission`` and
``effects`` stand in. The shape of the decision is the same: prerequisites first (an ordering
refusal is not a policy question), ``open`` skips the policy call, ``readonly`` denies anything
not declared ``read`` — fail-closed, as for MCP tools — and everything else is
``governance.evaluate_action`` with the same context keys the MCP path records, so a policy
written for "provider/container/member" reads an agent call without a new vocabulary.
"""

from __future__ import annotations

from swarmkit_runtime import prerequisites
from swarmkit_runtime._run_scope import current_run_id
from swarmkit_runtime.governance import GovernanceProvider
from swarmkit_runtime.prerequisites import Requires
from swarmkit_runtime.telemetry import record_governance_decision

from ._spec import AgentSkillSpec


async def check_agent_permission(
    spec: AgentSkillSpec,
    governance: GovernanceProvider | None,
    *,
    agent_id: str,
    skill_id: str,
    scopes: frozenset[str] = frozenset(),
    requires: Requires | None = None,
) -> tuple[bool, str]:
    """``(allowed, reason)`` for calling the agent this skill names."""
    unmet = prerequisites.missing(
        requires, run_id=current_run_id(), agent_id=agent_id, skill_id=skill_id
    )
    if unmet:
        record_governance_decision(decision="deny", scope="agent:call")
        return False, prerequisites.refusal(skill_id, unmet)

    if spec.permission == "readonly" and spec.effects != "read":
        record_governance_decision(decision="deny", scope="agent:call")
        return (
            False,
            f"skill '{skill_id}' is readonly and its effects are '{spec.effects}'; only a skill "
            "declaring `effects: read` may be called under readonly",
        )
    if spec.permission == "open" or governance is None:
        return True, ""

    kind = "topology" if spec.is_local else "card"
    decision = await governance.evaluate_action(
        agent_id=agent_id,
        action=f"agent:call:{kind}:{spec.target}",
        scopes_required=scopes,
        context={
            "server_permission": spec.permission,
            "effects": spec.effects,
            "provider": "agent",
            "container": kind,
            "member": spec.target,
        },
    )
    record_governance_decision(decision="allow" if decision.allowed else "deny", scope="agent:call")
    return decision.allowed, decision.reason
