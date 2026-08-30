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

__all__ = [
    "MCPServerClass",
    "build_low_level_server",
    "mcp_server_class",
    "tool_input_schema",
    "tool_output_schema",
    "tool_read_only_hint",
]

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


def build_low_level_server(
    name: str,
    *,
    list_tools: Any,
    call_tool: Any,
) -> Any:
    """A low-level ``Server`` with tools/list + tools/call registered, on either SDK.

    The third rename, and the one that reached a user as ``'Server' object has no attribute
    'list_tools'``: 1.x registered handlers with ``@server.list_tools()`` decorators, 2.0 removed
    them and takes ``on_list_tools=`` / ``on_call_tool=`` on the constructor, with a different
    handler signature (a request context and typed params) and a different return type (a result
    model rather than a bare list).

    Callers write ONE neutral form and this adapts it:

    * ``list_tools() -> list[Tool]``
    * ``call_tool(name, arguments) -> list[ContentBlock]``
    """
    import inspect  # noqa: PLC0415

    from mcp.server import Server  # noqa: PLC0415

    if "on_list_tools" in inspect.signature(Server.__init__).parameters:  # SDK 2.0
        from mcp.types import CallToolResult, ListToolsResult  # noqa: PLC0415

        async def _on_list(_ctx: Any, _params: Any) -> Any:
            return ListToolsResult(tools=await list_tools())

        async def _on_call(_ctx: Any, params: Any) -> Any:
            content = await call_tool(params.name, params.arguments or {})
            return CallToolResult(content=content)

        # mypy resolves `Server` against the INSTALLED 1.x stubs, where these kwargs do not
        # exist; the branch only runs when the signature says they do.
        server_cls: Any = Server
        return server_cls(name, on_list_tools=_on_list, on_call_tool=_on_call)

    server: Any = Server(name)  # SDK 1.x
    server.list_tools()(list_tools)
    server.call_tool()(call_tool)
    return server


def tool_read_only_hint(tool: Any) -> bool | None:
    """The tool's ``readOnlyHint`` annotation, or ``None`` when the server supplies none.

    A *hint*, in the protocol's own words — the server's claim about its own tool, not something
    the workspace author controls. So a declared `effects` entry always wins over it; this is only
    consulted when the workspace has not said.
    """
    annotations = _read(tool, "annotations")
    if annotations is None:
        return None
    hint = _read(annotations, "read_only_hint", "readOnlyHint")
    return bool(hint) if hint is not None else None
