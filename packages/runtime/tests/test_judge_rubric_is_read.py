"""`funnel.judge.rubric` reaches the judge, and the reachability report names each binding.

Two dead-config items, fixed together because they are the same complaint one level apart.

**`judge.rubric`** was declared in the Funnel schema, accepted by validation, displayed by
`swarmkit validate` — and read by nothing. `build_decision_judge` took `skill` and `threshold` and
ignored it, so every workspace that wanted a rubric had to repeat it inside the skill prompt, and
the declared file sat there looking like it was doing something. With per-kind rubrics coming, the
choice was wire it or delete it; deleting a field people have already written is the worse of the
two, and `evaluate_decision_skill` already had a `context` seam that reaches the prompt.

**The reachability report** printed `all 12 declared bindings are wired`. That is a summary OF a
check, not the check: with per-kind funnels a reader wants to know WHICH twelve, and an aggregate
hides the case where the twelve are not the twelve they meant to declare.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime.governance import DecisionSkillResult
from swarmkit_runtime.langgraph_compiler._gate_funnel import build_decision_judge

RUBRIC = "## Criteria\n- every claim cites a file\n- no invented table names\n"


class _Gov:
    """Captures what the judge was asked, so the rubric can be asserted where it lands."""

    def __init__(self, verdict: str = "pass", confidence: float = 1.0) -> None:
        self.calls: list[dict[str, Any]] = []
        self._verdict, self._confidence = verdict, confidence

    async def evaluate_decision_skill(self, **kwargs: Any) -> DecisionSkillResult:
        self.calls.append(kwargs)
        return DecisionSkillResult(
            skill_id=str(kwargs.get("skill_id")),
            verdict=self._verdict,  # type: ignore[arg-type]
            confidence=self._confidence,
            reasoning="scripted",
        )


def _funnel(tmp_path: Path, rubric_path: str | None) -> Path:
    """A funnel file on disk, so the rubric resolves the way a workspace's would."""
    (tmp_path / "funnels").mkdir(parents=True, exist_ok=True)
    (tmp_path / "rubrics").mkdir(parents=True, exist_ok=True)
    (tmp_path / "rubrics" / "spec.md").write_text(RUBRIC)
    path = tmp_path / "funnels" / "spec-review.yaml"
    path.write_text(yaml.safe_dump({"judge": {"skill": "artifact-judge"}}))
    return path


def _spec(rubric: str | None) -> dict[str, Any]:
    judge: dict[str, Any] = {"skill": "artifact-judge", "threshold": 0.8}
    if rubric is not None:
        judge["rubric"] = rubric
    return {"judge": judge}


# ---- the rubric reaches the judge ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_declared_rubric_is_passed_to_the_judge(tmp_path: Path) -> None:
    """The report: declared, validated, displayed, read by nothing."""
    source = _funnel(tmp_path, "rubrics/spec.md")
    gov = _Gov()

    judge = build_decision_judge(
        _spec("rubrics/spec.md"),
        governance=gov,
        agent_id="designer",
        declared_in=source,
        workspace_root=tmp_path,
    )
    assert judge is not None
    await judge("the artifact")

    assert gov.calls[0]["context"]["rubric"] == RUBRIC


@pytest.mark.asyncio
async def test_a_rubric_resolves_against_the_declaring_funnel_too(tmp_path: Path) -> None:
    """The schema calls it workspace-relative, so that base wins — but a path written relative to
    the funnel should not silently produce a judge with no rubric."""
    source = _funnel(tmp_path, None)
    (tmp_path / "funnels" / "beside.md").write_text(RUBRIC)
    gov = _Gov()

    judge = build_decision_judge(
        _spec("beside.md"),
        governance=gov,
        agent_id="designer",
        declared_in=source,
        workspace_root=tmp_path,
    )
    assert judge is not None
    await judge("the artifact")

    assert gov.calls[0]["context"]["rubric"] == RUBRIC


@pytest.mark.asyncio
async def test_no_rubric_sends_no_context(tmp_path: Path) -> None:
    """A funnel without a rubric must not start sending an empty one."""
    gov = _Gov()

    judge = build_decision_judge(_spec(None), governance=gov, agent_id="designer")
    assert judge is not None
    await judge("the artifact")

    assert gov.calls[0]["context"] is None


@pytest.mark.asyncio
async def test_an_unreadable_rubric_warns_and_the_judge_still_runs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A judge that stops working because a path is wrong is worse than one judging without the
    document — and silence is worse than both."""
    source = _funnel(tmp_path, None)
    gov = _Gov()

    with caplog.at_level("WARNING"):
        judge = build_decision_judge(
            _spec("rubrics/not-written.md"),
            governance=gov,
            agent_id="designer",
            declared_in=source,
            workspace_root=tmp_path,
        )
        assert judge is not None
        await judge("the artifact")

    assert "not-written.md" in caplog.text
    assert gov.calls, "the judge must still have run"
    assert gov.calls[0]["context"] is None


@pytest.mark.asyncio
async def test_the_threshold_still_decides(tmp_path: Path) -> None:
    """Wiring the rubric must not disturb what the layer is for."""
    source = _funnel(tmp_path, "rubrics/spec.md")
    judge = build_decision_judge(
        _spec("rubrics/spec.md"),
        governance=_Gov(verdict="pass", confidence=0.5),
        agent_id="designer",
        declared_in=source,
        workspace_root=tmp_path,
    )
    assert judge is not None

    assert (await judge("x")).passed is False


# ---- and it renders as a document, not a JSON blob -----------------------------------------------


def test_the_rubric_gets_its_own_section_in_the_prompt() -> None:
    """`Context: {"rubric": "## Criteria\\n..."}` buries the document in escapes. The judge is meant
    to read and score against it."""
    from swarmkit_runtime.governance._decision_evaluator import _build_input  # noqa: PLC0415

    text = _build_input("the artifact", "post_output", "designer", {"rubric": RUBRIC})

    assert "--- RUBRIC (score the content against this) ---" in text
    assert "every claim cites a file" in text
    assert "\\n" not in text, "the rubric must not arrive JSON-escaped"


def test_other_context_still_reaches_the_prompt() -> None:
    """The rubric is special-cased; everything else keeps its existing route."""
    from swarmkit_runtime.governance._decision_evaluator import _build_input  # noqa: PLC0415

    text = _build_input("x", "post_output", "designer", {"rubric": RUBRIC, "ticket": "WMS-30"})

    assert "WMS-30" in text


# ---- the reachability report names each binding -------------------------------------------------


def test_a_wired_binding_is_named_not_counted() -> None:
    """`all 12 declared bindings are wired` is a summary of a check, not the check."""
    from swarmkit_runtime.cli._cmd_authoring import _reachable_line  # noqa: PLC0415
    from swarmkit_runtime.reachability import Declaration  # noqa: PLC0415

    line = _reachable_line(
        Declaration(
            kind="funnel_layer",
            key="designer:spec-review:judge",
            declared_on="funnel spec-review",
            detail="the judge layer",
        )
    )

    assert "designer:spec-review:judge" in line
    assert "wired" in line


def test_a_required_wired_binding_says_so() -> None:
    from swarmkit_runtime.cli._cmd_authoring import _reachable_line  # noqa: PLC0415
    from swarmkit_runtime.reachability import Declaration  # noqa: PLC0415

    line = _reachable_line(
        Declaration(kind="decision_skill", key="x", declared_on="topology t", required=True)
    )

    assert "REQUIRED" in line


def test_the_json_output_already_listed_them() -> None:
    """`--json` carried the detail all along, which is why this was a rendering gap and not a
    missing check."""
    from swarmkit_runtime.reachability import (  # noqa: PLC0415
        Declaration,
        WiringLedger,
        compute_reachability,
    )

    ledger = WiringLedger()
    ledger.wired("funnel", "a:b")
    report = compute_reachability(
        [Declaration(kind="funnel", key="a:b", declared_on="agent a")], ledger
    )

    payload = json.dumps(report.to_dict())
    assert "a:b" in payload
