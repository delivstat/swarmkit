"""An `output_schema` must not stop a model node calling its tools.

Bug 14, reported against 1.145.1. `_build_completion_request` attached `response_format` whenever a
schema resolved, with no regard for whether the same request carried tools — and
`_to_openai_kwargs` duly sent both. Under structured-output enforcement the reply is constrained to
the schema grammar, and a `tool_calls` response is not in that grammar, so a model handed both has
no legal way to call a tool.

It complies the only way it can: by filling the schema with stubs that *say* it needs the tools.

    {"blocked_by": [{"reason": "Need to call required tools first",
                     "unblock": "Call get-spec-schema and get-spec-example first"}], ...}

Nothing errors. The document validates. The stage parks and a reviewer is shown a well-formed spec
built from no evidence — a `corpus_gaps` array asserting documentation is absent when no search was
ever run. A conformance check cannot catch it, because the artifact is valid. Only the audit shows
the tools were never called.

The reporting workspace had already felt this without naming it: its archetype comment records
trimming 20 tools to 13 because "tool schemas and output_schema share one grammar budget" and the
provider "refused to compile either". One grammar is exactly the problem — the tools and the schema
were being compiled together, and the schema won.

The runtime already had the right structure: the synthesis turn in `_tool_loop` passes NO tools,
because that is the turn that produces the document. The schema belongs there and only there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from swarmkit_runtime.langgraph_compiler._prompts import _build_completion_request
from swarmkit_runtime.model_providers import Message


@dataclass
class _Agent:
    id: str = "designer"
    role: str = "root"
    model: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    output_schema_disabled: bool = False
    skills: list[Any] = field(default_factory=list)


SCHEMA = {"type": "object", "required": ["spec"], "properties": {"spec": {"type": "string"}}}
MESSAGES = [Message(role="user", content="design the RF screens")]


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = name
        self.input_schema: dict[str, Any] = {"type": "object", "properties": {}}


def _request(agent: _Agent, tools: list[Any]) -> Any:
    return _build_completion_request("m", MESSAGES, "sys", tools, agent)  # type: ignore[arg-type]


# ---- the bug ---------------------------------------------------------------------------------


def test_a_turn_carrying_tools_is_not_grammar_locked() -> None:
    """The bug itself: both were sent, so no `tool_calls` reply was legal."""
    req = _request(_Agent(output_schema=SCHEMA), [_Tool("search-sterling-docs")])

    assert req.tools, "the tools must still be offered"
    assert req.response_format is None, (
        "a schema on a tool-carrying turn leaves the model no legal way to call a tool"
    )


def test_the_document_turn_still_gets_the_schema() -> None:
    """The synthesis turn passes no tools — that is the turn the schema is for, and it must keep
    it, or structured output would simply be gone."""
    req = _request(_Agent(output_schema=SCHEMA), [])

    assert req.response_format is not None
    assert req.response_format["json_schema"]["schema"] == SCHEMA


def test_an_agent_with_no_schema_is_unchanged() -> None:
    req = _request(_Agent(), [_Tool("search")])
    assert req.response_format is None
    assert req.tools


def test_the_worker_platform_default_is_also_deferred() -> None:
    """`role: worker` with no explicit schema still gets the platform default — which used to
    suppress tools on every worker in the delegation pattern, without anyone declaring anything."""
    req = _request(_Agent(role="worker"), [_Tool("search")])
    assert req.response_format is None, "the implicit schema must not suppress tools either"

    assert _request(_Agent(role="worker"), []).response_format is not None


def test_an_explicit_opt_out_is_respected_either_way() -> None:
    agent = _Agent(role="worker", output_schema_disabled=True)
    assert _request(agent, [_Tool("s")]).response_format is None
    assert _request(agent, []).response_format is None


# ---- the document is still constrained ---------------------------------------------------------


def test_a_schema_bound_agent_does_not_return_from_a_tool_turn() -> None:
    """Deferring the schema is only safe if the document is still produced on a turn that has it.

    The loop can return text directly from a tool-carrying turn; for a schema-bound agent that text
    would be unconstrained, leaving only validate-and-correct between it and the artifact. It now
    falls through to synthesis instead — one call, deterministic.
    """
    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_tool_loop.py"
    ).read_text()
    early_return = src.split('_progress(f"  [{agent.id}] tool limit reached')[0]
    assert "_schema_bound" in early_return
    assert "and not _schema_bound" in early_return, (
        "a schema-bound agent must reach the synthesis turn, which is where the schema is applied"
    )


def test_the_synthesis_turn_passes_no_tools() -> None:
    """The property the whole fix rests on: if synthesis ever started carrying tools, the schema
    would silently stop being applied anywhere."""
    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_tool_loop.py"
    ).read_text()
    expected = "synthesis_req = _build_completion_request("
    assert expected in src
    synthesis = src.split(expected)[1].split(")")[0]
    assert "[]" in synthesis, "synthesis must pass no tools, or the schema applies nowhere"


# ---- and the silence is broken -------------------------------------------------------------------


def test_zero_tool_calls_with_a_schema_is_surfaced() -> None:
    """The report's second ask. The output is valid and the run is green either way, so nothing
    else would ever say the research did not happen."""
    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_tool_loop.py"
    ).read_text()
    assert "made no tool" in src
    assert "_schema_bound and tools and not tool_results" in src
