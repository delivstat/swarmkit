"""The one governed MCP-call path (executor-mcp-gateway.md, invariant #4).

Every MCP tool call — whether from a model agent's ``mcp_tool`` skill or a harness talking through
the gateway — must pass the same governance gate: resolve the server/tool permission tier and, for
anything but ``open``, run ``GovernanceProvider.evaluate_action`` (and record the decision). This
module is that single chokepoint, so no caller can route around it.

- :func:`check_mcp_permission` — the gate; returns ``(allowed, reason)``.
- :func:`governed_mcp_call` — gate **then** ``MCPClientManager.call_tool``; raises
  :class:`MCPCallDenied` on refusal. The convenience the gateway uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from swarmkit_runtime import prerequisites
from swarmkit_runtime._run_scope import current_run_id
from swarmkit_runtime.prerequisites import Requires
from swarmkit_runtime.telemetry import record_governance_decision

if TYPE_CHECKING:
    from swarmkit_runtime.governance import GovernanceProvider
    from swarmkit_runtime.mcp._client import MCPClientManager, ToolResponse


class MCPCallDenied(RuntimeError):
    """A governed MCP call was refused by policy (carries the governance reason)."""


async def check_mcp_permission(
    mcp_manager: MCPClientManager | None,
    governance: GovernanceProvider | None,
    *,
    agent_id: str,
    server_id: str,
    tool_name: str,
    scopes: frozenset[str] = frozenset(),
    skill_id: str = "",
    requires: Requires | None = None,
    run_id: str | None = None,
) -> tuple[bool, str]:
    """Resolve the permission tier and, for anything but ``open``, run
    ``governance.evaluate_action`` (recording the decision). Returns ``(allowed, reason)``.

    ``open`` tools, or the absence of a governance provider, are allowed without a policy call —
    unchanged from the original skill-executor behaviour.

    **A declared prerequisite is a second reason to deny** (skill-prerequisites.md). It is checked
    here, and nowhere else, because both executors already dispatch through this one function and
    both already surface the reason to the agent as a tool error it can act on — so one enforcement
    point covers the model path and the harness gateway, and neither can drift from the other.

    It is checked *before* the policy call: an ordering refusal is not a policy question, and asking
    the governance provider to evaluate a call that is about to be refused anyway would put a
    misleading allow in the record.

    ``run_id`` is explicit rather than read from the run scope, because the gateway serves harness
    tool calls on uvicorn's tasks, which do not inherit it — the run scope would be empty exactly
    where the harness path needs it.
    """
    unmet = prerequisites.missing(
        requires,
        run_id=run_id if run_id is not None else current_run_id(),
        agent_id=agent_id,
        skill_id=skill_id,
    )
    if unmet:
        record_governance_decision(decision="deny", scope="mcp:call")
        return False, prerequisites.refusal(skill_id, unmet)

    permission = (
        mcp_manager.get_permission(server_id, tool_name) if mcp_manager is not None else "cautious"
    )
    if permission == "open" or governance is None:
        return True, ""
    decision = await governance.evaluate_action(
        agent_id=agent_id,
        action=f"mcp:call:{server_id}:{tool_name}",
        scopes_required=scopes,
        context={"server_permission": permission},
    )
    record_governance_decision(decision="allow" if decision.allowed else "deny", scope="mcp:call")
    return decision.allowed, decision.reason


async def governed_mcp_call(
    mcp_manager: MCPClientManager,
    governance: GovernanceProvider | None,
    *,
    agent_id: str,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    scopes: frozenset[str] = frozenset(),
    skill_id: str = "",
    requires: Requires | None = None,
    run_id: str | None = None,
) -> ToolResponse:
    """Gate the call through :func:`check_mcp_permission`, then invoke it on the server. Raises
    :class:`MCPCallDenied` when policy refuses — the caller never reaches an ungoverned call.

    A call that returns without raising and without ``isError`` satisfies this skill's own
    prerequisite for anything that requires it. A call that raised, or that the server flagged as an
    error, does not: nothing was learned, so nothing is unlocked.
    """
    allowed, reason = await check_mcp_permission(
        mcp_manager,
        governance,
        agent_id=agent_id,
        server_id=server_id,
        tool_name=tool_name,
        scopes=scopes,
        skill_id=skill_id,
        requires=requires,
        run_id=run_id,
    )
    if not allowed:
        raise MCPCallDenied(reason)
    response = await mcp_manager.call_tool(server_id, tool_name, arguments)
    if not getattr(response.data, "isError", False):
        prerequisites.note_satisfied(
            run_id=run_id if run_id is not None else current_run_id(),
            agent_id=agent_id,
            skill_id=skill_id,
        )
    return response


__all__ = ["MCPCallDenied", "check_mcp_permission", "governed_mcp_call"]
