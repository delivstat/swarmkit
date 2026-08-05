"""A harness's tool grant names the tools the gateway actually advertises.

Reported as "the harness does not find tools that are in the gateway". Three namespaces were in
play and none of them agreed:

===========================  ==========================================
your topology / `mcp_tools`  ``search-wms-tables``       (the skill id)
the gateway advertises       ``sterling__search_docs``   (``<server>__<tool>``)
Claude Code exposes          ``mcp__swarmkit__sterling__search_docs``
===========================  ==========================================

`_task_spec` filled `TaskSpec.mcp_tools` from `skill.id` — a name that exists nowhere downstream —
and then nothing read the field at all: it was assigned in one place and consumed in none, the
same shape as the `context_files` gap. The only thing reaching `--allowedTools` was
`config.allowed_tools`, written by hand.

So an operator who constrained an agent naturally wrote the skill id — the name the topology uses
everywhere else — and allowlisted a tool that exists under no such name, putting every real tool
outside the grant. Since an *unset* grant means all tools, **constraining the agent was what broke
it**, which is the wrong way round.

Two rules hold the fix in place. The mangling is declared by the adapter, not coded in Python,
because `mcp__<server>__<tool>` is Claude Code's convention, not a universal one. And an unset
grant is left alone: turning an MCP grant into an allowlist would silently drop the built-ins the
agent also needs, and a restriction nobody asked for is not a fix for one that was too tight."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from swarmkit_runtime.executors._adapter_spec import parse_adapter_spec
from swarmkit_runtime.executors._declarative import _merged_tool_grant
from swarmkit_runtime.executors._run import TaskSpec
from swarmkit_runtime.mcp._gateway import GATEWAY_NAME_SEP, GATEWAY_SERVER_NAME, build_gateway_tools

ADAPTERS = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/executors/adapters"

GRANTED = [
    ("sterling", "search_docs", "search the docs"),
    ("wms", "get_table", "read a table"),
]


class _Spec:
    """Only the field the grant needs — the adapter's declared mangling."""

    def __init__(self, template: str) -> None:
        self.launch = type("_Launch", (), {"mcp_tool_name": template})()


def _load(name: str) -> Any:
    """The shipped adapter, as the runtime loads it — not a copy of what it should say."""
    return parse_adapter_spec(yaml.safe_load((ADAPTERS / f"{name}.yaml").read_text()))


def _task(*tools: str) -> TaskSpec:
    return TaskSpec(statement="do the thing", mcp_tools=tools)


CLAUDE = _Spec("mcp__{gateway}__{tool}")


# ---- the names line up ------------------------------------------------------------------------


def test_the_gateway_name_is_server_then_tool() -> None:
    """The middle namespace, pinned: the adapter reconstructs this shape, so it is a contract."""

    class _Manager:
        def get_tool_input_schema(self, *_a: Any) -> dict[str, Any]:
            return {"type": "object"}

    names = [t.name for t in build_gateway_tools(GRANTED, _Manager())]  # type: ignore[arg-type]

    assert names == ["sterling__search_docs", "wms__get_table"]
    assert GATEWAY_NAME_SEP == "__"


def test_the_grant_gains_the_harness_native_names() -> None:
    """The bug, directly: a grant written in skill ids matched nothing the harness could call."""
    grant = _merged_tool_grant(
        _task("sterling__search_docs"), CLAUDE, {"allowed_tools": "Read,Write"}
    )

    assert f"mcp__{GATEWAY_SERVER_NAME}__sterling__search_docs" in grant


def test_the_operators_own_entries_survive() -> None:
    """Their grant is the point of the setting — the MCP names are added to it, not instead of it.
    An agent that loses Read and Write to gain its MCP tools has not been helped."""
    grant = _merged_tool_grant(
        _task("sterling__search_docs"), CLAUDE, {"allowed_tools": "Read,Bash"}
    )

    assert grant.startswith("Read,Bash")
    assert "Read" in grant and "Bash" in grant


def test_every_granted_tool_appears() -> None:
    grant = _merged_tool_grant(
        _task("sterling__search_docs", "wms__get_table"), CLAUDE, {"allowed_tools": "Read"}
    )

    assert "mcp__swarmkit__sterling__search_docs" in grant
    assert "mcp__swarmkit__wms__get_table" in grant


def test_a_name_the_operator_already_wrote_is_not_duplicated() -> None:
    """Someone who has worked out the mangled name should not see it twice."""
    already = "mcp__swarmkit__sterling__search_docs"

    grant = _merged_tool_grant(_task("sterling__search_docs"), CLAUDE, {"allowed_tools": already})

    assert grant.count(already) == 1


def test_a_space_separated_grant_is_understood() -> None:
    """`--allowedTools` accepts either separator, so both have to dedupe."""
    grant = _merged_tool_grant(
        _task("sterling__search_docs"),
        CLAUDE,
        {"allowed_tools": "Read mcp__swarmkit__sterling__search_docs"},
    )

    assert grant.count("mcp__swarmkit__sterling__search_docs") == 1


# ---- an unset grant is left alone -------------------------------------------------------------


def test_no_grant_stays_no_grant() -> None:
    """Unset means all tools. Turning MCP grants into an allowlist here would silently drop Read,
    Write and Bash — a restriction nobody asked for, in the name of fixing one that was too tight.
    """
    assert _merged_tool_grant(_task("sterling__search_docs"), CLAUDE, {}) == ""


def test_an_agent_with_no_gateway_tools_keeps_its_grant_verbatim() -> None:
    assert _merged_tool_grant(_task(), CLAUDE, {"allowed_tools": "Read,Write"}) == "Read,Write"


# ---- the mangling is data, not code -----------------------------------------------------------


def test_the_template_comes_from_the_adapter() -> None:
    """Executors are data: a harness quirk belongs in its adapter. A different harness declaring a
    different shape must get that shape, with no Python change."""
    grant = _merged_tool_grant(
        _task("sterling__search_docs"), _Spec("tools/{tool}"), {"allowed_tools": "Read"}
    )

    assert "tools/sterling__search_docs" in grant
    assert "mcp__" not in grant


def test_an_adapter_that_declares_nothing_uses_the_flat_name() -> None:
    """The default must be the gateway's own name, not a guess at some harness's convention."""
    grant = _merged_tool_grant(
        _task("sterling__search_docs"), _Spec("{tool}"), {"allowed_tools": "Read"}
    )

    assert "sterling__search_docs" in grant


def test_claude_code_declares_its_mangling() -> None:
    """Read from the shipped adapter, so the fix is verified against what actually loads."""
    spec = _load("claude-code")

    assert spec.launch.mcp_tool_name == "mcp__{gateway}__{tool}"


def test_the_other_bundled_adapters_default_rather_than_guess() -> None:
    """codex, gemini-cli and opencode have not had their grant syntax verified. Declaring a shape
    on their behalf would produce a grant that silently matches nothing — the very bug being fixed.
    """
    for name in ("codex", "gemini-cli", "opencode"):
        spec = _load(name)
        assert spec.launch.mcp_tool_name == "{tool}", f"{name} declares an unverified mangling"
