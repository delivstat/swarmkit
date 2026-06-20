"""Eval runner tests — deterministic, no real model (fake runtime)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from swarmkit_runtime.eval import EvalCase, EvalSet, ExpectSpec, run_eval_set
from swarmkit_runtime.eval._models import EvalSetMeta
from swarmkit_runtime.governance import DecisionSkillResult


class _FakeRuntime:
    """Stands in for WorkspaceRuntime: scripted output + judge verdict."""

    def __init__(self, output: str, *, verdict: str = "pass", confidence: float = 0.9) -> None:
        self._output = output
        self._verdict = verdict
        self._confidence = confidence
        self.raise_on_run = False

    async def run(self, topology_name: str, user_input: str) -> object:
        if self.raise_on_run:
            raise RuntimeError("boom")
        return SimpleNamespace(output=self._output)

    async def judge(
        self, *, skill_id: str, content: str, trigger: str = "eval"
    ) -> DecisionSkillResult:
        return DecisionSkillResult(
            skill_id=skill_id,
            verdict=self._verdict,  # type: ignore[arg-type]
            confidence=self._confidence,
            reasoning="fake",
        )


def _set(*cases: EvalCase, target: str = "hello") -> EvalSet:
    return EvalSet(metadata=EvalSetMeta(id="t"), target=target, cases=list(cases))


@pytest.mark.asyncio
async def test_deterministic_pass_and_fail() -> None:
    rt = _FakeRuntime("Hello engineers!")
    es = _set(
        EvalCase(id="ok", input="x", expect=ExpectSpec(contains=["engineer"], not_empty=True)),
        EvalCase(id="bad", input="x", expect=ExpectSpec(contains=["managers"])),
    )
    report = await run_eval_set(rt, es)
    assert report.total == 2 and report.passed == 1 and report.failed == 1
    by_id = {c.case_id: c for c in report.cases}
    assert by_id["ok"].passed is True
    assert by_id["bad"].passed is False
    assert report.pass_rate == 0.5


@pytest.mark.asyncio
async def test_judge_pass_then_fail() -> None:
    es = _set(EvalCase(id="j", input="x", expect=ExpectSpec(judge="tone", min_confidence=0.6)))
    assert (await run_eval_set(_FakeRuntime("hi", verdict="pass", confidence=0.9), es)).passed == 1
    # verdict pass but confidence below threshold -> fail
    assert (await run_eval_set(_FakeRuntime("hi", verdict="pass", confidence=0.4), es)).passed == 0
    # verdict fail -> fail
    assert (await run_eval_set(_FakeRuntime("hi", verdict="fail", confidence=0.9), es)).passed == 0


@pytest.mark.asyncio
async def test_per_case_error_isolation() -> None:
    rt = _FakeRuntime("anything")
    rt.raise_on_run = True
    es = _set(EvalCase(id="boom", input="x", expect=ExpectSpec(not_empty=True)))
    report = await run_eval_set(rt, es)
    assert report.passed == 0
    assert report.cases[0].error is not None and "boom" in report.cases[0].error


@pytest.mark.asyncio
async def test_no_expectations_is_smoke_test() -> None:
    # no expectations -> passes as long as the run completes
    es = _set(EvalCase(id="smoke", input="x", expect=ExpectSpec()))
    report = await run_eval_set(_FakeRuntime("whatever"), es)
    assert report.passed == 1


@pytest.mark.asyncio
async def test_report_to_dict_shape() -> None:
    es = _set(EvalCase(id="ok", input="x", expect=ExpectSpec(contains=["hi"])))
    report = await run_eval_set(_FakeRuntime("hi there"), es)
    d = report.to_dict()
    assert d["eval_set_id"] == "t" and d["target"] == "hello"
    assert d["total"] == 1 and d["passed"] == 1 and d["pass_rate"] == 1.0
    # nested structure via the typed report (to_dict returns dict[str, object])
    assert report.cases[0].case_id == "ok"
    assert report.cases[0].checks[0].name == "contains"
