"""Demo: gatewayed MCP for harness executors (executor-mcp-gateway.md).

A harness (Claude Code / opencode) reaches the workspace's MCP tools through an ephemeral in-process
gateway that routes every call through governance + audit — never a direct, ungoverned call. This
demo stands up the real SSE gateway and drives it with SwarmKit's own MCP client (standing in for
the harness, so no `claude` binary is needed), showing:

  1. the harness-native MCP config SwarmKit generates (what goes to --mcp-config);
  2. the harness listing + calling a workspace tool through the gateway;
  3. the mcp:call audit event proving the call was governed (not a direct call);
  4. a denied tier being refused at the gateway;
  5. the container-reachability config (advertise host.docker.internal).

Run it:

    uv run python packages/runtime/demos/mcp_gateway.py
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from swarmkit_runtime.mcp._gateway import GatewayTool, harness_mcp_config, mcp_gateway


def _bar(label: str) -> None:
    print(f"\n--- {label}")


class _Resp:
    def __init__(self, text: str) -> None:
        self.data = type("D", (), {"content": [type("B", (), {"text": text})()]})()


class _WorkspaceMCP:
    """Stand-in for the workspace's MCP servers — the gateway calls this after governance allows."""

    def __init__(self, permission: str) -> None:
        self._perm = permission
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def get_permission(self, server_id: str, tool_name: str) -> str:
        return self._perm

    def get_tool_input_schema(self, server_id: str, tool_name: str) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def call_tool(
        self, server_id: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> _Resp:
        self.calls.append((server_id, tool_name, arguments))
        return _Resp(f"contents of {arguments}")


class _Governance:
    """The governance layer — allows/denies per tier and records the audit trail."""

    def __init__(self, allow: bool) -> None:
        self._allow = allow
        self.audit: list[dict[str, Any]] = []

    async def evaluate_action(self, *, agent_id: str, action: str, **_: object) -> Any:
        self.audit.append({"agent": agent_id, "action": action})
        reason = "" if self._allow else "policy: deny"
        return type("D", (), {"allowed": self._allow, "reason": reason})()

    async def record_event(self, event: Any) -> None: ...


async def _drive(gw_url: str, token: str, tool: str, args: dict[str, Any]) -> str:
    from mcp import ClientSession  # noqa: PLC0415
    from mcp.client.sse import sse_client  # noqa: PLC0415

    async with (
        sse_client(gw_url, headers={"Authorization": f"Bearer {token}"}) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        names = [t.name for t in listed.tools]
        result = await session.call_tool(tool, args)
        return f"tools={names}  →  {result.content[0].text}"  # type: ignore[union-attr]


async def main() -> None:
    tools = [GatewayTool("fs__read_file", "fs", "read_file", "Read a file", {"type": "object"})]

    _bar("1. Governed run: the gateway routes the harness's call through governance + audit")
    mcp, gov = _WorkspaceMCP("cautious"), _Governance(allow=True)
    async with mcp_gateway(tools, mcp, gov, agent_id="coding-worker") as gw:
        print("  --mcp-config SwarmKit writes for the harness:")
        cfg = json.dumps(harness_mcp_config(gw.url, "<token>")["mcpServers"], indent=2)
        print("   ", cfg.replace("\n", "\n    "))
        out = await _drive(gw.url, gw.token, "fs__read_file", {"path": "app.py"})
        print(f"  harness (via gateway): {out}")
    print(f"  audit trail: {gov.audit}")
    print(f"  reached the real MCP server: {mcp.calls}")

    _bar("2. A denied tier is refused at the gateway — the real server is never touched")
    mcp2, gov2 = _WorkspaceMCP("strict"), _Governance(allow=False)
    async with mcp_gateway(tools, mcp2, gov2, agent_id="coding-worker") as gw:
        out = await _drive(gw.url, gw.token, "fs__read_file", {"path": "secret"})
        print(f"  harness (via gateway): {out}")
    print(f"  audit trail: {gov2.audit}")
    print(f"  reached the real MCP server: {mcp2.calls}  (empty — refused before the call)")

    _bar("3. Container reachability: bind 0.0.0.0, advertise host.docker.internal")
    async with mcp_gateway(
        tools,
        mcp,
        gov,
        agent_id="coding-worker",
        host="0.0.0.0",
        advertise_host="host.docker.internal",
    ) as gw:
        print(f"  URL the container connects to: {gw.url}")
        print("  container run adds: --add-host host.docker.internal:host-gateway")
        print("  and host.docker.internal is auto-added to the egress allowlist")

    print("\nOK — a harness reaches the workspace's MCP tools, every call governed + audited.")


if __name__ == "__main__":
    asyncio.run(main())
