"""Demo: declarative skill prerequisites (design/details/skill-prerequisites.md).

The reported failure, reproduced and then fixed — no model calls, no API budget.

The failure: a workspace granted `get-build-convention` and instructed the agent, in the archetype
and in the prompt, to call `list-build-conventions` first. It was called **0** times. In a single
run, same agent, same prompt, an ack-gated tool was called 4 times and the merely-requested one 0.
The variable is not the prompt; it is whether the tool refuses service.

The fix is four lines of topology::

    skills: [list-build-conventions, get-build-convention]
    requires:
      get-build-convention: [list-build-conventions]

Run it:

    uv run python packages/runtime/demos/skill_prerequisites.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from swarmkit_runtime import prerequisites
from swarmkit_runtime.mcp._gateway import build_gateway_tools
from swarmkit_runtime.mcp._governed import MCPCallDenied, check_mcp_permission, governed_mcp_call

RUN = "demo-run"
AGENT = "builder"
REQUIRES = {"get-build-convention": ("list-build-conventions",)}


class _Server:
    """Stands in for the workspace's MCP servers — the point of the demo is that it is never
    reached until the ordering rule is satisfied."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_permission(self, *_a: str) -> str:
        return "open"

    def get_tool_input_schema(self, *_a: str) -> dict[str, Any]:
        return {"type": "object"}

    async def call_tool(self, server: str, tool: str, args: Any = None) -> Any:
        self.calls.append(tool)
        text = "CONV-1 CONV-2 CONV-3" if tool == "list_build_conventions" else "use the RF pattern"
        return type(
            "R",
            (),
            {
                "data": type("D", (), {"content": [], "isError": False})(),
                "metadata": type("M", (), {"source": server, "duration_ms": 1})(),
                "text": text,
            },
        )()


async def main() -> None:
    server = _Server()
    print("=" * 78)
    print("BEFORE — the rule is a request in a prompt")
    print("=" * 78)
    allowed, _ = await check_mcp_permission(
        None, None, agent_id=AGENT, server_id="wms", tool_name="get_build_convention"
    )
    print(f"  the agent skips straight to get-build-convention → allowed={allowed}")
    print("  0 calls to list-build-conventions, as reported.\n")

    print("=" * 78)
    print("AFTER — `requires:` in the topology")
    print("=" * 78)

    print("\n1. the agent reaches for the guarded skill first")
    try:
        await governed_mcp_call(
            server,  # type: ignore[arg-type]
            None,
            agent_id=AGENT,
            server_id="wms",
            tool_name="get_build_convention",
            skill_id="get-build-convention",
            requires=REQUIRES,
            run_id=RUN,
        )
    except MCPCallDenied as exc:
        print(f"   REFUSED: {exc}")
    print(f"   the server was never touched: calls so far = {server.calls}")

    print("\n2. the refusal named what to do, so the agent does it")
    await governed_mcp_call(
        server,  # type: ignore[arg-type]
        None,
        agent_id=AGENT,
        server_id="wms",
        tool_name="list_build_conventions",
        skill_id="list-build-conventions",
        requires=REQUIRES,
        run_id=RUN,
    )
    satisfied = sorted(prerequisites.satisfied_for(RUN, AGENT))
    print(f"   list-build-conventions ran; satisfied = {satisfied}")

    print("\n3. the retry succeeds — recovery inside the same loop")
    await governed_mcp_call(
        server,  # type: ignore[arg-type]
        None,
        agent_id=AGENT,
        server_id="wms",
        tool_name="get_build_convention",
        skill_id="get-build-convention",
        requires=REQUIRES,
        run_id=RUN,
    )
    print(f"   calls, in order: {server.calls}")

    print("\n4. the same rule holds on a harness agent")
    tools = build_gateway_tools(
        [("wms", "get_build_convention", "", "get-build-convention")],
        server,  # type: ignore[arg-type]
    )
    print(f"   the gateway advertises {tools[0].name} as skill {tools[0].skill_id!r},")
    print("   and dispatches through the same seam — one enforcement point, both executors.")

    print("\n5. a sibling agent's call does not satisfy this agent's prerequisite")
    unmet = prerequisites.missing(
        REQUIRES, run_id=RUN, agent_id="reviewer", skill_id="get-build-convention"
    )
    print(f"   reviewer still owes: {list(unmet)}")

    prerequisites.forget_run(RUN)
    print("\ndone.")


if __name__ == "__main__":
    asyncio.run(main())
