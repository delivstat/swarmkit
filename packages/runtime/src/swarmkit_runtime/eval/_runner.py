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
from swarmkit_runtime.eval._models import CheckResult, EvalCaseResult, EvalReport, EvalSet
from swarmkit_runtime.governance import DecisionSkillResult

_MAX_OUTPUT = 2000


class _RuntimeLike(Protocol):
    """The subset of WorkspaceRuntime the runner needs (keeps it test-friendly)."""

    async def run(self, topology_name: str, user_input: str) -> object: ...

    async def judge(
        self, *, skill_id: str, content: str, trigger: str = "eval"
    ) -> DecisionSkillResult: ...


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


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
            if case.expect.judge:
                verdict = await runtime.judge(skill_id=case.expect.judge, content=output)
                ok = verdict.verdict == "pass" and verdict.confidence >= case.expect.min_confidence
                checks.append(
                    CheckResult(
                        f"judge:{case.expect.judge}",
                        ok,
                        f"{verdict.verdict} ({verdict.confidence:.2f}) {verdict.reasoning[:120]}",
                    )
                )
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
