"""Decision skills run on a harness executor, including `required: true`.

Reported against 1.133.0. `node_fn()` handed off to the harness runner with an early `return`, and
every decision-skill gate sat after it — so `_ds_bindings` was computed for the agent and then
discarded, and `run_harness_node()` had no parameter to receive them either.

Observed on `wms-design`: the agent returned a markdown document where the topology required a JSON
object. `spec-conformance` (`required: true`) would have returned `verdict: fail` and triggered a
revision. It never ran, and the markdown became the run's final output.

The failure was silent and inverted from the safe direction. `required: true` reads as "this gate
must pass"; on a harness it meant nothing. `swarmkit validate` reported no error, because the
binding is structurally valid. The trace showed a normal successful node with no "skipped" marker.
And it was executor-dependent: a topology validated on a model node changed behaviour when switched
to `executor.kind: harness` with no other edit and no warning.

The retry is the interesting part. `_make_retry_fn` re-prompts a MODEL with the previous output —
"the agent doesn't re-run tools, it revises using data it already has" — which is wrong twice over
here: it needs a `model_provider` a harness agent may not have, and a harness's output is the
product of work in a sandbox, so revising its text with a different model would produce a
description of a fix rather than the fix. A harness retry re-invokes the harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
import swarmkit_runtime.langgraph_compiler._harness_node as hn
from swarmkit_runtime.governance import DecisionSkillBinding
from swarmkit_runtime.langgraph_compiler import _compiler
from swarmkit_runtime.langgraph_compiler._compiler import _run_harness_with_gates

MARKDOWN = "# WMS Design\n\nA prose document where a JSON object was required."
JSON_SPEC = '{"screens": [], "resources": []}'


@dataclass
class _Verdict:
    verdict: str
    skill_id: str = "spec-conformance"
    reasoning: str = "output is markdown; a JSON object conforming to the spec is required"
    flagged_items: list[str] = field(default_factory=list)


class _Governance:
    """Records what was asked of it; fails until the output looks like JSON."""

    def __init__(self, *, always_fail: bool = False) -> None:
        self.always_fail = always_fail
        self.calls: list[tuple[str, str]] = []

    async def record_event(self, _event: Any) -> None:
        return None

    async def evaluate_decision_skill(
        self, *, skill_id: str, trigger: str, agent_id: str, content: str, context: Any = None
    ) -> _Verdict:
        self.calls.append((skill_id, content))
        ok = content.strip().startswith("{") and not self.always_fail
        return _Verdict(verdict="pass" if ok else "fail", skill_id=skill_id)


@dataclass
class _Executor:
    kind: str = "harness"
    ref: str = "claude-code"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Agent:
    id: str = "designer"
    role: str = "designer"
    executor: _Executor = field(default_factory=_Executor)
    skills: list[Any] = field(default_factory=list)
    model: dict[str, Any] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)


def _binding(**kw: Any) -> DecisionSkillBinding:
    return DecisionSkillBinding(
        id=kw.pop("id", "spec-conformance"),
        trigger=kw.pop("trigger", "post_output"),
        required=kw.pop("required", True),
        config=kw.pop("config", {}),
        **kw,
    )


def _harness_stub(outputs: list[str], invocations: list[str]) -> Any:
    """Stands in for `run_harness_node`, recording the statement it was given each time."""

    async def _run(agent: Any, state: Any, governance: Any, **_kw: Any) -> dict[str, Any]:
        invocations.append(str(state.get("input", "")))
        text = outputs[min(len(invocations) - 1, len(outputs) - 1)]
        return {
            "current_agent": agent.id,
            "agent_results": {agent.id: text},
            "messages": [],
            "output": text,
        }

    return _run


@pytest.fixture
def patch_harness(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch the module `_run_harness_with_gates` imports from, at its import site."""

    def _install(outputs: list[str]) -> list[str]:
        invocations: list[str] = []
        monkeypatch.setattr(hn, "run_harness_node", _harness_stub(outputs, invocations))
        return invocations

    return _install


async def _run(
    governance: Any, bindings: list[DecisionSkillBinding], state: Any = None
) -> dict[str, Any]:
    return await _run_harness_with_gates(
        _Agent(),  # type: ignore[arg-type]
        state or {"input": "design the RF screens"},  # type: ignore[arg-type]
        governance,
        agent_id="designer",
        bindings=bindings,
    )


# ---- the bug -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_post_output_skill_runs_on_a_harness_node(patch_harness: Any) -> None:
    """The bug itself: the skill was never invoked."""
    patch_harness([MARKDOWN, JSON_SPEC])
    gov = _Governance()

    await _run(gov, [_binding()])

    assert gov.calls, "the decision skill never ran — `required: true` meant nothing"
    assert gov.calls[0][1] == MARKDOWN, "it must judge what the harness actually produced"


@pytest.mark.asyncio
async def test_no_bindings_changes_nothing(patch_harness: Any) -> None:
    """The overwhelmingly common case must be untouched."""
    patch_harness([MARKDOWN])
    gov = _Governance()

    result = await _run(gov, [])

    assert result["output"] == MARKDOWN
    assert gov.calls == []


# ---- the retry is driven by the harness, not a model ---------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_gate_re_invokes_the_harness(patch_harness: Any) -> None:
    """The design decision. A model retry would revise the harness's TEXT; only re-running the
    harness can produce a corrected artifact from work in its sandbox."""
    invocations = patch_harness([MARKDOWN, JSON_SPEC])
    gov = _Governance()

    result = await _run(gov, [_binding()])

    assert len(invocations) == 2, "the harness must be re-invoked, not a model re-prompted"
    assert result["output"] == JSON_SPEC


@pytest.mark.asyncio
async def test_the_retry_carries_the_gate_reasoning(patch_harness: Any) -> None:
    """Without the reasoning the re-run is a repeat of the same work — the same defect as the
    rework loop that dropped reviewer comments."""
    invocations = patch_harness([MARKDOWN, JSON_SPEC])

    await _run(_Governance(), [_binding()])

    assert "JSON object" in invocations[1], "the second attempt must know what to fix"
    assert "design the RF screens" in invocations[1], "and must keep the original task"


@pytest.mark.asyncio
async def test_the_revision_replaces_what_flows_downstream(patch_harness: Any) -> None:
    """A revision recorded only in `output` would leave the next node reading the text the gate just
    rejected, out of `agent_results` or the message."""
    patch_harness([MARKDOWN, JSON_SPEC])

    result = await _run(_Governance(), [_binding()])

    assert result["output"] == JSON_SPEC
    assert result["agent_results"]["designer"] == JSON_SPEC
    assert [m.content for m in result["messages"]] == [JSON_SPEC]


@pytest.mark.asyncio
async def test_retries_are_bounded_and_the_output_is_flagged(patch_harness: Any) -> None:
    """Each retry is a full harness run, so an unbounded loop is real money. When they are
    exhausted the output passes through ANNOTATED — never silently."""
    invocations = patch_harness([MARKDOWN])
    gov = _Governance(always_fail=True)

    result = await _run(gov, [_binding(config={"max_retries": 2})])

    assert len(invocations) == 3, "one attempt plus two retries"
    assert "GOVERNANCE FLAGS" in result["output"]


@pytest.mark.asyncio
async def test_max_retries_zero_does_not_re_invoke(patch_harness: Any) -> None:
    """`max_retries: 0` is how an operator says "check, but do not pay to try again"."""
    invocations = patch_harness([MARKDOWN])

    await _run(_Governance(always_fail=True), [_binding(config={"max_retries": 0})])

    assert len(invocations) == 1


# ---- a failed harness is not a non-conforming output ---------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_harness_is_not_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gating a failure asks a decision skill to judge an error string, and a `required` skill would
    then retry — paying for a full harness run to fix a sandbox that could not start."""
    calls: list[str] = []

    async def _failing(agent: Any, state: Any, governance: Any, **_kw: Any) -> dict[str, Any]:
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
    gov = _Governance(always_fail=True)

    result = await _run(gov, [_binding()])

    assert gov.calls == [], "a failed harness must not be judged as output"
    assert len(calls) == 1, "and must not be retried"
    assert result["node_errors"], "the failure marker survives for the stage seam to act on"


# ---- the model path is unchanged ----------------------------------------------------------------


def test_pre_input_gates_still_precede_execution_for_every_executor() -> None:
    """`pre_input` moved ABOVE the executor dispatch: it gates the input, which is
    executor-agnostic, and refusing after paying for a harness run would be a strange way to
    decline. This pins the ordering, which is the whole point of the move."""
    src = (__import__("pathlib").Path(_compiler.__file__)).read_text()
    pre_input = src.index("---- pre_input decision skills")
    dispatch = src.index("---- executor dispatch")
    memory = src.index("---- workspace memory (pre_input context injection)")
    assert pre_input < dispatch, "a relevance gate must run before the harness launches"
    assert memory < dispatch, "the harness must see the injected memory context"


def test_the_dispatch_inside_node_fn_goes_through_the_gated_wrapper() -> None:
    """Regression guard for the exact shape of the bug: node_fn's executor dispatch calling
    `run_harness_node` directly, which skips every gate below it. Scoped to the dispatch region —
    the wrapper itself calls `run_harness_node`, and must."""
    src = (__import__("pathlib").Path(_compiler.__file__)).read_text()
    start = src.index("---- executor dispatch")
    region = src[start : src.index("---- already-delegated fast path")]
    assert "_run_harness_with_gates(" in region
    assert "run_harness_node(" not in region, (
        "node_fn must not call the harness runner directly, or the gates are skipped again"
    )
