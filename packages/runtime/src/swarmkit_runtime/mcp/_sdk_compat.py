"""One place that knows the MCP SDK renamed things between 1.x and 2.0.

Two renames, and the runtime was half-migrated for each — so on any given SDK version one call
site worked and the other raised ``AttributeError``:

* ``Tool.inputSchema`` (1.x) became ``Tool.input_schema`` (2.0). ``mcp/_client.py`` read the old
  name and ``mcp/_gateway.py`` read the new one, so **every stdio MCP server failed to start on
  2.0**, and the gateway would have failed the same way on 1.x. The client's failure was the
  dangerous one: servers were skipped with a warning, the run completed, and an agent whose job is
  to ground its answer in tool results answered from nothing and exited 0.
* ``mcp.server.fastmcp.FastMCP`` became ``mcp.server.mcpserver.MCPServer``. Four bundled servers
  still imported the old path and died at import on 2.0 — reported only as "Connection closed",
  because a subprocess that dies during import looks identical to one that hung up.

**Constructing** is not affected and must not be "fixed": ``Tool(inputSchema=...)`` works on both,
because ``inputSchema`` is the wire field name in the MCP spec and 2.0 keeps it as the alias. Only
attribute *reads* off a parsed model differ. Every read goes through here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Typecheck against the 1.x class — it is the one with stubs, and the two are compatible for
    # the constructor + `@server.tool()` surface the bundled servers use. Resolving this through
    # the try/except below would widen it to Any and silently untype every tool in every server.
    from mcp.server.fastmcp import FastMCP as MCPServerClass
else:
    try:  # SDK 2.0
        from mcp.server.mcpserver import MCPServer as MCPServerClass
    except ImportError:  # SDK 1.x
        from mcp.server.fastmcp import FastMCP as MCPServerClass

__all__ = ["MCPServerClass", "mcp_server_class", "tool_input_schema", "tool_output_schema"]

_MISSING = object()


def _read(obj: Any, *names: str) -> Any:
    for name in names:
        value = getattr(obj, name, _MISSING)
        if value is not _MISSING:
            return value
    return None


def tool_input_schema(tool: Any) -> dict[str, Any]:
    """A tool's input schema, whichever SDK parsed it. ``{}`` when it has none."""
    schema = _read(tool, "input_schema", "inputSchema")
    return dict(schema) if schema else {}


def tool_output_schema(tool: Any) -> dict[str, Any] | None:
    """A tool's output schema, or None. Present on 2.0; absent on most of 1.x."""
    schema = _read(tool, "output_schema", "outputSchema")
    return dict(schema) if schema else None


def mcp_server_class() -> Any:
    """The server class to build a bundled MCP server with — ``MCPServer`` on 2.0, ``FastMCP`` on
    1.x. Prefer importing :data:`MCPServerClass` directly; this exists for callers that must
    resolve it lazily (``server/_mcp.py`` only imports MCP at all when the package is present)."""
    return MCPServerClass
