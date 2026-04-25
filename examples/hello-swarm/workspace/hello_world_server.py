"""hello-world MCP server — backs the say-hello skill in the on-ramp example.

A single stdio MCP tool, ``greet(audience)``, that returns a static
greeting string. Stays as small as possible so readers can focus on how
the skill, the workspace's ``mcp_servers`` block, and the runtime fit
together — not on what an MCP server has to do.

Run directly to sanity-check it speaks MCP:

    uv run python examples/hello-swarm/workspace/hello_world_server.py

Or — the path the runtime takes — let ``swarmkit run`` launch it as a
subprocess via the workspace's ``mcp_servers`` config.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

server = FastMCP("hello-world")


@server.tool()
def greet(audience: str = "world") -> str:
    """Return a greeting addressed to ``audience``."""
    audience = audience.strip() or "world"
    return f"Hello, {audience}! Welcome to SwarmKit."


if __name__ == "__main__":
    server.run()
