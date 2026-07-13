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
) -> tuple[bool, str]:
    """Resolve the permission tier and, for anything but ``open``, run
    ``governance.evaluate_action`` (recording the decision). Returns ``(allowed, reason)``.

    ``open`` tools, or the absence of a governance provider, are allowed without a policy call —
    unchanged from the original skill-executor behaviour."""
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
) -> ToolResponse:
    """Gate the call through :func:`check_mcp_permission`, then invoke it on the server. Raises
    :class:`MCPCallDenied` when policy refuses — the caller never reaches an ungoverned call."""
    allowed, reason = await check_mcp_permission(
        mcp_manager,
        governance,
        agent_id=agent_id,
        server_id=server_id,
        tool_name=tool_name,
        scopes=scopes,
    )
    if not allowed:
        raise MCPCallDenied(reason)
    return await mcp_manager.call_tool(server_id, tool_name, arguments)


__all__ = ["MCPCallDenied", "check_mcp_permission", "governed_mcp_call"]
