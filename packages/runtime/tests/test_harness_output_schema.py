"""`output_schema` is enforced on a harness executor.

Gap #3. `_harness_node.py` contained zero references to `output_schema`, so a harness agent had
neither a schema constraint nor a post-hoc decision-skill check (gap #2, fixed in 1.142.0) — the two
independent mechanisms that would each have caught a non-conforming output. On `wms-design` that is
why an agent could return a markdown document where the topology declared a JSON object, and the run
reported success.

The correction is driven by the agent's own executor, for the same reason the decision-skill retry
is: a model asked to fix the JSON would be editing a description of work done in a sandbox it cannot
reach.

**Only an explicitly declared schema is enforced here.** The model path uses
`get_effective_output_schema`, which falls back to the worker platform default
(`{findings: [{fact, source}], …}`) for any `role: worker` with no explicit schema. Applying that on
this path would silently impose a findings-schema on every harness worker — `examples/sdlc-pipeline`
alone has a `developer` archetype that is `role: worker` + `kind: harness` with no schema, and it
produces a diff, not findings. Every run of it would begin failing validation and burning full
harness retries against a contract nobody wrote.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
import swarmkit_runtime.langgraph_compiler._harness_node as hn
from swarmkit_runtime.langgraph_compiler._compiler import _run_harness_with_gates
from swarmkit_runtime.langgraph_compiler._harness_node import _harness_output_schema

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["screens"],
    "properties": {"screens": {"type": "array"}},
}
CONFORMING = json.dumps({"screens": [{"id": "PGM"}]})
MARKDOWN = "# WMS Design\n\nA prose document where a JSON object was required."
MISSING_FIELD = json.dumps({"resources": []})


class _Governance:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def record_event(self, event: Any) -> None:
        self.events.append(event)

    async def evaluate_decision_skill(self, *, content: str = "", **_kw: Any) -> Any:
        raise AssertionError("no decision skills are bound in these tests")


@dataclass
class _Executor:
    kind: str = "harness"
    ref: str = "claude-code"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Agent:
    id: str = "designer"
    role: str = "worker"
    executor: _Executor = field(default_factory=_Executor)
    skills: list[Any] = field(default_factory=list)
    model: dict[str, Any] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    output_schema_disabled: bool = False


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(outputs: list[str]) -> list[str]:
        seen: list[str] = []

        async def _run(agent: Any, state: Any, _gov: Any, **_kw: Any) -> dict[str, Any]:
            seen.append(str(state.get("input", "")))
            text = outputs[min(len(seen) - 1, len(outputs) - 1)]
            return {
                "current_agent": agent.id,
                "agent_results": {agent.id: text},
                "messages": [],
                "output": text,
            }

        monkeypatch.setattr(hn, "run_harness_node", _run)
        return seen

    return _install


async def _run(agent: _Agent, governance: Any) -> dict[str, Any]:
    return await _run_harness_with_gates(
        agent,  # type: ignore[arg-type]
        cast("Any", {"input": "design the RF screens"}),
        governance,
        agent_id="designer",
        bindings=[],
    )


# ---- the gap -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_declared_schema_is_enforced(harness: Any) -> None:
    """The bug: `output_schema` was ignored entirely on this path."""
    seen = harness([MARKDOWN, CONFORMING])
    result = await _run(_Agent(output_schema=SCHEMA), _Governance())

    assert len(seen) == 2, "a non-conforming result must be sent back for correction"
    assert result["output"] == CONFORMING


@pytest.mark.asyncio
async def test_the_correction_names_the_offending_fields(harness: Any) -> None:
    """Field-specific errors, not "try again" — the same reason the model path builds a targeted
    correction prompt."""
    seen = harness([MISSING_FIELD, CONFORMING])
    await _run(_Agent(output_schema=SCHEMA), _Governance())

    assert "screens" in seen[1], "the harness must be told WHICH field is wrong"
    assert "design the RF screens" in seen[1], "and must keep the original task"


@pytest.mark.asyncio
async def test_a_conforming_result_is_untouched(harness: Any) -> None:
    seen = harness([CONFORMING])
    result = await _run(_Agent(output_schema=SCHEMA), _Governance())

    assert len(seen) == 1, "no correction round for output that already conforms"
    assert result["output"] == CONFORMING


@pytest.mark.asyncio
async def test_exhausted_retries_annotate_rather_than_pass_silently(harness: Any) -> None:
    """The whole point of the gap was output that failed a declared contract and looked fine."""
    seen = harness([MARKDOWN])
    gov = _Governance()
    result = await _run(_Agent(output_schema=SCHEMA), gov)

    # TWO, not three. This harness returns the identical draft to every correction, and a
    # re-invocation that changes nothing ends the loop (bug 19) rather than spending a further
    # full harness session to receive the same string a third time. The contract was still
    # checked and still failed, so the violation below is unchanged.
    assert len(seen) == 2, "one attempt plus one correction that did not move"
    assert "OUTPUT SCHEMA VIOLATIONS" in result["output"]
    assert any(getattr(e, "event_type", "") == "output.schema_violation" for e in gov.events), (
        "and the violation is auditable, not only visible in the text"
    )


@pytest.mark.asyncio
async def test_the_corrected_text_replaces_what_flows_downstream(harness: Any) -> None:
    harness([MARKDOWN, CONFORMING])
    result = await _run(_Agent(output_schema=SCHEMA), _Governance())

    assert result["output"] == CONFORMING
    assert result["agent_results"]["designer"] == CONFORMING
    assert [m.content for m in result["messages"]] == [CONFORMING]


# ---- only what the author declared ---------------------------------------------------------------


def test_a_worker_without_a_schema_gets_no_platform_default() -> None:
    """The regression this change must not introduce. `examples/sdlc-pipeline`'s `developer` is
    `role: worker` + `kind: harness` with no schema and produces a diff; forcing the findings-schema
    on it would fail every run and burn full harness retries against a contract nobody wrote."""
    assert _harness_output_schema(_Agent(role="worker")) is None  # type: ignore[arg-type]


def test_an_explicit_schema_is_returned() -> None:
    assert _harness_output_schema(_Agent(output_schema=SCHEMA)) == SCHEMA  # type: ignore[arg-type]


def test_an_explicit_opt_out_wins() -> None:
    agent = _Agent(output_schema=SCHEMA, output_schema_disabled=True)
    assert _harness_output_schema(agent) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_no_schema_means_no_validation_round(harness: Any) -> None:
    """A harness agent with no declared schema behaves exactly as before."""
    seen = harness([MARKDOWN])
    result = await _run(_Agent(), _Governance())

    assert len(seen) == 1
    assert result["output"] == MARKDOWN


# ---- interaction with the rest of the path -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_harness_is_not_schema_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validating an error string against a schema would burn retries trying to make
    `[harness:claude-code] failure: no result event` parse as JSON."""
    calls: list[str] = []

    async def _failing(agent: Any, state: Any, _gov: Any, **_kw: Any) -> dict[str, Any]:
        calls.append("run")
        text = "[harness:claude-code] failure: no result event"
        return {
            "current_agent": agent.id,
            "agent_results": {agent.id: text},
            "messages": [],
            "output": text,
            "node_errors": {agent.id: text},
        }

    monkeypatch.setattr(hn, "run_harness_node", _failing)
    result = await _run(_Agent(output_schema=SCHEMA), _Governance())

    assert len(calls) == 1, "a failed harness must not be retried for schema conformance"
    assert result["node_errors"]


@pytest.mark.asyncio
async def test_schema_runs_before_the_decision_skills(harness: Any) -> None:
    """Ordering matters: a decision skill should judge output that already satisfies its declared
    shape, not spend a `required` retry on a shape violation the schema layer can name exactly."""
    harness([MARKDOWN, CONFORMING])
    judged: list[str] = []

    class _Gov(_Governance):
        async def evaluate_decision_skill(self, *, content: str = "", **_kw: Any) -> Any:
            judged.append(content)
            return type(
                "V", (), {"verdict": "pass", "skill_id": "s", "reasoning": "", "flagged_items": []}
            )()

    from swarmkit_runtime.governance import DecisionSkillBinding  # noqa: PLC0415

    await _run_harness_with_gates(
        _Agent(output_schema=SCHEMA),  # type: ignore[arg-type]
        cast("Any", {"input": "design the RF screens"}),
        cast("Any", _Gov()),
        agent_id="designer",
        bindings=[DecisionSkillBinding(id="s", trigger="post_output")],
    )

    assert judged == [CONFORMING], "the skill must see the schema-corrected output"
