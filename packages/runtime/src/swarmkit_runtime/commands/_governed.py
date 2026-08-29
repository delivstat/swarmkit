"""The one governed command-call path — the sibling of ``mcp/_governed.py``.

Nothing about the permission model is new here, and that is the point. ``check_mcp_permission``
resolves a tier from configuration and hands ``GovernanceProvider.evaluate_action`` an action
string plus the scopes the skill declared; that machinery was never MCP-specific. A pack is the
container, a command the member, and the two callers differ only in what they put in the audit
payload.

One thing *is* better on this side: ``readonly`` does not have to guess. The MCP path infers
write-ness by substring-scanning the action name (see issue #825), which lets ``truncate_table``
through and stops ``send_query``. A command declares ``effects``, so the tier is applied to a fact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from swarmkit_runtime import prerequisites
from swarmkit_runtime._run_scope import current_run_id
from swarmkit_runtime.telemetry import record_governance_decision

if TYPE_CHECKING:
    from swarmkit_runtime.commands._config import CommandPackConfig, CommandSpecConfig
    from swarmkit_runtime.governance import GovernanceProvider
    from swarmkit_runtime.prerequisites import Requires


def action_for(pack_id: str, command_id: str) -> str:
    """The governance action string for a command call.

    A sibling of ``mcp:call:<server>:<tool>``, not a replacement for it. Actions are labels — the
    runtime authorizes on ``iam.required_scopes`` and nothing pattern-matches this string — so
    renaming the MCP namespace to unify them would have cost audit-history continuity for a string
    nothing reads. The structure lives in the audit payload instead; see :func:`audit_payload`.
    """
    return f"command:call:{pack_id}:{command_id}"


def audit_payload(pack: CommandPackConfig, spec: CommandSpecConfig) -> dict[str, object]:
    """Structured fields describing the call, for the decision context and the audit record.

    Carried as fields rather than encoded in the action string so a later query matches
    ``provider`` and ``effects`` directly instead of parsing a colon-joined name it would have to
    segment correctly.
    """
    return {
        "provider": "command",
        "container": pack.pack_id,
        "member": spec.command_id,
        "effects": spec.effects,
    }


async def check_command_permission(
    pack: CommandPackConfig | None,
    spec: CommandSpecConfig | None,
    governance: GovernanceProvider | None,
    *,
    agent_id: str,
    pack_id: str,
    command_id: str,
    scopes: frozenset[str] = frozenset(),
    skill_id: str = "",
    requires: Requires | None = None,
    run_id: str | None = None,
) -> tuple[bool, str]:
    """Resolve the tier and, for anything but ``open``, run ``evaluate_action``.

    Returns ``(allowed, reason)``. Prerequisites are checked first, for the same reason they are on
    the MCP path: an ordering refusal is not a policy question, and evaluating a call that is about
    to be refused anyway would put a misleading allow in the record.
    """
    unmet = prerequisites.missing(
        requires,
        run_id=run_id if run_id is not None else current_run_id(),
        agent_id=agent_id,
        skill_id=skill_id,
    )
    if unmet:
        record_governance_decision(decision="deny", scope="command:call")
        return False, prerequisites.refusal(skill_id, unmet)

    if pack is None:
        return False, f"workspace declares no command pack '{pack_id}'"
    if spec is None:
        known = ", ".join(sorted(pack.commands)) or "none"
        return False, f"command pack '{pack_id}' has no command '{command_id}' (has: {known})"

    permission = pack.permission_for(command_id)

    # Declared, not sniffed. This is the fix issue #825 wants on the MCP side.
    if permission == "readonly" and spec.effects == "write":
        record_governance_decision(decision="deny", scope="command:call")
        return False, (
            f"command pack '{pack_id}' is readonly and command '{command_id}' declares "
            f"effects: write"
        )

    if permission == "open" or governance is None:
        return True, ""

    decision = await governance.evaluate_action(
        agent_id=agent_id,
        action=action_for(pack_id, command_id),
        scopes_required=scopes,
        context={"server_permission": permission, **audit_payload(pack, spec)},
    )
    record_governance_decision(
        decision="allow" if decision.allowed else "deny", scope="command:call"
    )
    return decision.allowed, decision.reason
