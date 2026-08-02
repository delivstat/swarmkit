"""The MCP SDK renamed things between 1.x and 2.0, and the runtime was half-migrated for each.

Reported from a real upgrade (bugs 04 and 05). Both failures were near-silent:

* Every stdio MCP server was skipped with a warning and the run **completed, exit 0, with no
  tools**. An agent whose job is grounding its answer in tool results answered from nothing, and
  the output read exactly like a correct one.
* The bundled servers died during import, which surfaces only as "Connection closed" — the symptom
  of a subprocess that hung up, not the cause.

These tests use fake tool objects rather than the installed SDK, so they pin BOTH shapes on
whichever version CI happens to run.
"""

from __future__ import annotations

from typing import Any, ClassVar

from swarmkit_runtime.mcp._sdk_compat import (
    MCPServerClass,
    tool_input_schema,
    tool_output_schema,
)

SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}}


class _Sdk1Tool:
    """mcp 1.x: camelCase only. `input_schema` does not exist."""

    name = "search"
    inputSchema: ClassVar[dict[str, Any]] = SCHEMA


class _Sdk2Tool:
    """mcp 2.0: snake_case only. `inputSchema` does not exist."""

    name = "search"
    input_schema: ClassVar[dict[str, Any]] = SCHEMA
    output_schema: ClassVar[dict[str, Any]] = {"type": "string"}


def test_reads_the_1x_shape() -> None:
    assert tool_input_schema(_Sdk1Tool()) == SCHEMA


def test_reads_the_2x_shape() -> None:
    """`_client.py` read only the 1.x name, so on 2.0 every server raised AttributeError at
    startup and was skipped — the run then answered with no tools and exited 0."""
    assert tool_input_schema(_Sdk2Tool()) == SCHEMA


def test_a_tool_with_no_schema_is_an_empty_dict_not_a_crash() -> None:
    class _Bare:
        name = "ping"

    assert tool_input_schema(_Bare()) == {}


def test_a_none_schema_is_an_empty_dict() -> None:
    class _NoneSchema:
        name = "ping"
        input_schema = None

    assert tool_input_schema(_NoneSchema()) == {}


def test_the_schema_is_copied_not_aliased() -> None:
    """The cache hands these out; a shared dict would let one tool's schema mutate another's."""
    tool = _Sdk2Tool()
    out = tool_input_schema(tool)
    out["injected"] = True
    assert "injected" not in tool.input_schema


def test_output_schema_reads_both_shapes_and_tolerates_absence() -> None:
    assert tool_output_schema(_Sdk2Tool()) == {"type": "string"}
    assert tool_output_schema(_Sdk1Tool()) is None


# ---- the wire name is NOT the bug, and must not be "fixed" ------------------------------------


def test_constructing_a_tool_with_the_wire_name_still_works() -> None:
    """`inputSchema=` is the MCP spec's field name and 2.0 keeps it as the alias, so every
    Tool(inputSchema=...) construction site is correct. Only attribute READS differ. This pins
    that, because the obvious over-correction is to rename the constructor arguments too.
    """
    from mcp.types import Tool  # noqa: PLC0415

    tool = Tool(name="x", inputSchema=SCHEMA)
    assert tool_input_schema(tool) == SCHEMA


# ---- the server class ---------------------------------------------------------------------


def test_the_server_class_resolves_and_is_constructible() -> None:
    """`mcp.server.fastmcp` is gone in 2.0. Four bundled servers imported it directly and died at
    import, reported only as 'Connection closed'."""
    server: Any = MCPServerClass("test-server")
    assert server is not None
    # The one API surface every bundled server uses beyond the constructor.
    assert callable(getattr(server, "tool", None))


def test_every_bundled_server_imports() -> None:
    """The regression was an ImportError at module scope, so importing IS the test."""
    import importlib  # noqa: PLC0415

    for module in (
        "swarmkit_runtime.docs_reader._server",
        "swarmkit_runtime.gate_validator._server",
        "swarmkit_runtime.knowledge._server",
    ):
        assert importlib.import_module(module) is not None


def test_no_module_imports_the_moved_path_directly() -> None:
    """One place knows about the rename. A direct `mcp.server.fastmcp` import anywhere else is a
    server that will die at import on SDK 2.0 — which is how four of them shipped."""
    from pathlib import Path  # noqa: PLC0415

    src = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime"
    offenders = [
        f"{path.relative_to(src)}:{num}"
        for path in src.rglob("*.py")
        if path.name != "_sdk_compat.py"
        for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "mcp.server.fastmcp" in line or "mcp.server.mcpserver" in line
    ]
    assert not offenders, "import MCPServerClass from mcp._sdk_compat instead:\n" + "\n".join(
        offenders
    )
