"""A decision skill binding's `config:` block actually reaches the skill.

The schema documents `config` as "skill-specific configuration" and accepts any shape. Nothing
read it: a binding could carry a whole tuning block — including `max_retries` — and every value
was silently discarded. Declarable, documented, inert, and impossible to notice from the outside,
because the default behaviour is indistinguishable from a config that was honoured and happened to
match the default.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.governance import DecisionSkillBinding, DecisionSkillResult
from swarmkit_runtime.langgraph_compiler._decision_gate import (
    evaluate_post_output,
    evaluate_pre_input,
)


class _RecordingGovernance:
    """Records what each evaluation was handed, and fails a fixed number of times first."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_times = fail_times

    async def evaluate_decision_skill(
        self,
        *,
        skill_id: str,
        trigger: str,
        agent_id: str,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> DecisionSkillResult:
        self.calls.append({"skill_id": skill_id, "trigger": trigger, "context": context})
        failing = len(self.calls) <= self._fail_times
        return DecisionSkillResult(
            skill_id=skill_id,
            verdict="fail" if failing else "pass",
            confidence=1.0,
            reasoning="not grounded" if failing else "ok",
        )


def _binding(**config: Any) -> DecisionSkillBinding:
    return DecisionSkillBinding(id="output-conformance", trigger="post_output", config=config)


@pytest.mark.asyncio
async def test_config_reaches_the_skill() -> None:
    """Otherwise `config:` is a block a user can write, validate, and never have applied."""
    gov = _RecordingGovernance()
    await evaluate_post_output(
        agent_id="coordinator",
        output="draft",
        bindings=[_binding(min_confidence=0.8, style="strict")],
        governance=gov,  # type: ignore[arg-type]
    )
    context = gov.calls[0]["context"]
    assert context is not None
    assert context["config"] == {"min_confidence": 0.8, "style": "strict"}


@pytest.mark.asyncio
async def test_config_cannot_overwrite_the_triggers_own_context() -> None:
    """The trigger's keys describe the thing being evaluated. A binding that could overwrite
    `task_ids` or `scope` would be rewriting the evidence the skill judges against."""
    gov = _RecordingGovernance()
    await evaluate_post_output(
        agent_id="coordinator",
        output="draft",
        bindings=[_binding(scope="forged")],
        governance=gov,  # type: ignore[arg-type]
        context={"scope": "the real frozen scope"},
    )
    assert gov.calls[0]["context"]["scope"] == "the real frozen scope"
    assert gov.calls[0]["context"]["config"] == {"scope": "forged"}


@pytest.mark.asyncio
async def test_no_config_leaves_the_context_untouched() -> None:
    """An empty block must not start injecting a `config` key into every evaluation."""
    gov = _RecordingGovernance()
    await evaluate_post_output(
        agent_id="coordinator",
        output="draft",
        bindings=[DecisionSkillBinding(id="x", trigger="post_output")],
        governance=gov,  # type: ignore[arg-type]
        context={"task_ids": ["t1"]},
    )
    assert gov.calls[0]["context"] == {"task_ids": ["t1"]}


@pytest.mark.asyncio
async def test_pre_input_gets_the_config_too() -> None:
    gov = _RecordingGovernance()
    await evaluate_pre_input(
        agent_id="coordinator",
        user_input="hello",
        bindings=[DecisionSkillBinding(id="x", trigger="pre_input", config={"k": 1})],
        governance=gov,  # type: ignore[arg-type]
    )
    assert gov.calls[0]["context"] == {"config": {"k": 1}}


# ---- max_retries ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_retries_from_config_is_honoured() -> None:
    """The default is 4. A binding asking for 1 must get 1 — the retry loop is the expensive part
    of governance, and 'it retried four times' is a bill, not a preference."""
    gov = _RecordingGovernance(fail_times=99)
    attempts = 0

    async def _retry(_feedback: str) -> str:
        nonlocal attempts
        attempts += 1
        return "revised"

    await evaluate_post_output(
        agent_id="coordinator",
        output="draft",
        bindings=[_binding(max_retries=1)],
        governance=gov,  # type: ignore[arg-type]
        retry_fn=_retry,
    )
    assert attempts == 1


@pytest.mark.asyncio
async def test_zero_retries_means_judge_once() -> None:
    gov = _RecordingGovernance(fail_times=99)
    attempts = 0

    async def _retry(_feedback: str) -> str:
        nonlocal attempts
        attempts += 1
        return "revised"

    output, _ = await evaluate_post_output(
        agent_id="coordinator",
        output="draft",
        bindings=[_binding(max_retries=0)],
        governance=gov,  # type: ignore[arg-type]
        retry_fn=_retry,
    )
    assert attempts == 0
    # Still annotated rather than blocked — exhausting retries never drops the output.
    assert "GOVERNANCE FLAGS" in output


@pytest.mark.asyncio
async def test_the_largest_declared_value_wins() -> None:
    """One shared loop, several bindings: a binding asking for more attempts should not be capped
    by a stricter sibling, since the loop exits as soon as they all pass."""
    gov = _RecordingGovernance(fail_times=99)
    attempts = 0

    async def _retry(_feedback: str) -> str:
        nonlocal attempts
        attempts += 1
        return "revised"

    await evaluate_post_output(
        agent_id="coordinator",
        output="draft",
        bindings=[
            DecisionSkillBinding(id="a", trigger="post_output", config={"max_retries": 1}),
            DecisionSkillBinding(id="b", trigger="post_output", config={"max_retries": 3}),
        ],
        governance=gov,  # type: ignore[arg-type]
        retry_fn=_retry,
    )
    assert attempts == 3


@pytest.mark.asyncio
async def test_a_nonsense_max_retries_falls_back_to_the_default() -> None:
    """A typo must not silently disable retries, which is the failure that hides."""
    gov = _RecordingGovernance(fail_times=99)
    attempts = 0

    async def _retry(_feedback: str) -> str:
        nonlocal attempts
        attempts += 1
        return "revised"

    await evaluate_post_output(
        agent_id="coordinator",
        output="draft",
        bindings=[_binding(max_retries="two")],
        governance=gov,  # type: ignore[arg-type]
        retry_fn=_retry,
    )
    assert attempts == 4  # _DEFAULT_MAX_RETRIES


# ---- a check that cannot read its own result must not report success quietly -------------------


def test_an_absent_verdict_warns_that_the_check_is_not_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`{"valid": false}` is the usual shape of a validator wired up without mapping its
    vocabulary. It has no verdict, so it defaults to pass — a validation layer reporting success
    on every rejection. Still passes (failing closed is its own outage) but no longer in silence.
    """
    from swarmkit_runtime.governance._decision_evaluator import _parse_result  # noqa: PLC0415

    with caplog.at_level("WARNING"):
        result = _parse_result("schema-conformance", '{"valid": false}')
    assert result.verdict == "pass"
    assert "no 'verdict' field" in caplog.text
    assert "schema-conformance" in caplog.text


def test_an_unreadable_verdict_warns_and_names_the_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`rejected` might plausibly mean fail — but guessing at a synonym invents a verdict the skill
    did not give. It defaults to pass, loudly, so the mapping gets fixed at the source."""
    from swarmkit_runtime.governance._decision_evaluator import _parse_result  # noqa: PLC0415

    with caplog.at_level("WARNING"):
        result = _parse_result("schema-conformance", '{"verdict": "rejected"}')
    assert result.verdict == "pass"
    assert "'rejected'" in caplog.text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"verdict": "FAIL"}', "fail"),
        ('{"verdict": "Fail"}', "fail"),
        ('{"verdict": " fail "}', "fail"),
        ('{"verdict": "needs_revision"}', "needs-revision"),
        ('{"verdict": "NEEDS-REVISION"}', "needs-revision"),
    ],
)
def test_verdict_form_is_normalised(
    raw: str, expected: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Casing and separators are not part of the meaning, and models vary both constantly. Reading
    `FAIL` as unrecognised meant defaulting it to PASS — fail-open on the check whose whole job is
    to fail closed, on a skill that plainly said fail."""
    from swarmkit_runtime.governance._decision_evaluator import _parse_result  # noqa: PLC0415

    with caplog.at_level("WARNING"):
        assert _parse_result("x", raw).verdict == expected
    assert caplog.text == ""  # a form difference is not a misconfiguration


def test_a_valid_verdict_warns_about_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """The warning has to stay rare enough to be worth reading."""
    from swarmkit_runtime.governance._decision_evaluator import _parse_result  # noqa: PLC0415

    with caplog.at_level("WARNING"):
        result = _parse_result("x", '{"verdict": "fail", "reasoning": "unsupported claim"}')
    assert result.verdict == "fail"
    assert caplog.text == ""


def test_a_fenced_mcp_response_with_provenance_still_parses(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MCP servers append `[source: ...]` and models fence their JSON. Neither is a mismatch, so
    neither may trip the warning."""
    from swarmkit_runtime.governance._decision_evaluator import _parse_result  # noqa: PLC0415

    raw = '```json\n{"verdict": "fail", "reasoning": "no"}\n```\n[source: rynko-flow]'
    with caplog.at_level("WARNING"):
        assert _parse_result("x", raw).verdict == "fail"
    assert caplog.text == ""
