"""Eval runner — run each case through a topology and score it.

Reuses the runtime's existing seams: ``run`` to execute the topology and ``judge``
(→ GovernanceProvider.evaluate_decision_skill) for the LLM rubric tier. Per-case
isolation: one failing case never aborts the run.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import Protocol

from swarmkit_runtime.eval._checks import deterministic_checks
from swarmkit_runtime.eval._models import (
    CheckResult,
    EvalCaseResult,
    EvalReport,
    EvalSet,
    ExpectSpec,
)
from swarmkit_runtime.governance import DecisionSkillResult

_MAX_OUTPUT = 2000


class _RuntimeLike(Protocol):
    """The subset of WorkspaceRuntime the runner needs (keeps it test-friendly)."""

    async def run(self, topology_name: str, user_input: str) -> object: ...

    async def judge(
        self, *, skill_id: str, content: str, trigger: str = "eval"
    ) -> DecisionSkillResult: ...

    async def judge_rubric(
        self, *, rubric: str, content: str, trigger: str = "eval"
    ) -> DecisionSkillResult: ...


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _judge_check(name: str, verdict: DecisionSkillResult, expect: ExpectSpec) -> CheckResult:
    ok = verdict.verdict == "pass" and verdict.confidence >= expect.min_confidence
    return CheckResult(
        name, ok, f"{verdict.verdict} ({verdict.confidence:.2f}) {verdict.reasoning[:120]}"
    )


def _trajectory_checks(run_result: object, expect: ExpectSpec) -> list[CheckResult]:
    """Assert which skills were invoked, from the run's events (RunEvent.skill_id)."""
    if not expect.used_skills:
        return []
    events = getattr(run_result, "events", None) or []
    invoked = {getattr(e, "skill_id", None) for e in events}
    return [
        CheckResult("used_skill", sid in invoked, sid if sid in invoked else f"{sid} not invoked")
        for sid in expect.used_skills
    ]


async def run_eval_set(
    runtime: _RuntimeLike,
    eval_set: EvalSet,
    *,
    on_case: Callable[[EvalCaseResult], None] | None = None,
) -> EvalReport:
    """Run every case against ``eval_set.target`` and score it. A case passes when
    its run did not error and all set expectations pass (a case with no expectations
    is a smoke test — passes if the run completes)."""
    started = _now()
    results: list[EvalCaseResult] = []
    for case in eval_set.cases:
        checks: list[CheckResult] = []
        output = ""
        error: str | None = None
        try:
            run_result = await runtime.run(eval_set.target, case.input)
            output = str(getattr(run_result, "output", "") or "")
            checks.extend(deterministic_checks(output, case.expect))
            checks.extend(_trajectory_checks(run_result, case.expect))
            if case.expect.judge:
                verdict = await runtime.judge(skill_id=case.expect.judge, content=output)
                checks.append(_judge_check(f"judge:{case.expect.judge}", verdict, case.expect))
            if case.expect.rubric:
                verdict = await runtime.judge_rubric(rubric=case.expect.rubric, content=output)
                checks.append(_judge_check("rubric", verdict, case.expect))
        except Exception as exc:
            error = str(exc)[:300]

        passed = error is None and all(c.passed for c in checks)
        case_result = EvalCaseResult(
            case_id=case.id,
            passed=passed,
            checks=checks,
            output=output[:_MAX_OUTPUT],
            error=error,
        )
        results.append(case_result)
        if on_case is not None:
            on_case(case_result)

    return EvalReport(
        eval_set_id=eval_set.metadata.id,
        target=eval_set.target,
        cases=results,
        started_at=started,
        finished_at=_now(),
    )
