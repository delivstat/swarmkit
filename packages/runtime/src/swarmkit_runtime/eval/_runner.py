"""Eval runner — run each case through a topology and score it.

Reuses the runtime's existing seams: ``run`` to execute the topology and ``judge``
(→ GovernanceProvider.evaluate_decision_skill) for the LLM rubric tier. Per-case
isolation: one failing case never aborts the run.
"""

from __future__ import annotations

import asyncio
import datetime
import inspect
from collections.abc import Awaitable, Callable, Collection
from typing import Protocol

from swarmkit_runtime.eval._checks import deterministic_checks
from swarmkit_runtime.eval._models import (
    CheckResult,
    EvalCase,
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

    async def judge_reference(
        self, *, reference: str, content: str, case_input: str = "", trigger: str = "eval"
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


def _case_cost(run_result: object) -> float:
    usage = getattr(run_result, "usage", None)
    return float(getattr(usage, "cost_usd", 0.0) or 0.0) if usage is not None else 0.0


async def _score_case(
    runtime: _RuntimeLike, eval_set: EvalSet, case: EvalCase, timeout_s: float | None
) -> EvalCaseResult:
    checks: list[CheckResult] = []
    output = ""
    error: str | None = None
    cost = 0.0
    try:
        # An eval case is measurement, not a run someone asked for. Writing a job row
        # per case would fill history with scoring traffic and make cost attribution
        # meaningless.
        # noqa: service-layer
        run_result = await asyncio.wait_for(runtime.run(eval_set.target, case.input), timeout_s)
        cost = _case_cost(run_result)
        output = str(getattr(run_result, "output", "") or "")
        checks.extend(deterministic_checks(output, case.expect))
        checks.extend(_trajectory_checks(run_result, case.expect))
        if case.expect.judge:
            verdict = await runtime.judge(skill_id=case.expect.judge, content=output)
            checks.append(_judge_check(f"judge:{case.expect.judge}", verdict, case.expect))
        if case.expect.rubric:
            verdict = await runtime.judge_rubric(rubric=case.expect.rubric, content=output)
            checks.append(_judge_check("rubric", verdict, case.expect))
        if case.expect.reference:
            verdict = await runtime.judge_reference(
                reference=case.expect.reference, content=output, case_input=case.input
            )
            checks.append(_judge_check("reference", verdict, case.expect))
    except TimeoutError:
        error = f"timeout: the case did not finish within {timeout_s:g}s"
    except Exception as exc:
        error = str(exc)[:300]

    return EvalCaseResult(
        case_id=case.id,
        passed=error is None and all(c.passed for c in checks),
        checks=checks,
        output=output[:_MAX_OUTPUT],
        error=error,
        cost_usd=cost,
    )


async def run_eval_set(
    runtime: _RuntimeLike,
    eval_set: EvalSet,
    *,
    on_case: Callable[[EvalCaseResult], Awaitable[None] | None] | None = None,
    case_timeout_s: float | None = None,
    should_stop: Callable[[], bool | Awaitable[bool]] | None = None,
    skip_case_ids: Collection[str] = (),
) -> EvalReport:
    """Run every case against ``eval_set.target`` and score it. A case passes when
    its run did not error and all set expectations pass (a case with no expectations
    is a smoke test — passes if the run completes).

    The keyword hooks all default off, so a bare call behaves as it always has:

    - ``on_case`` is called with each result as it lands, and awaited if it returns an awaitable
      (the eval service records the case durably there).
    - ``case_timeout_s`` bounds one case; a case that overruns fails with a timeout error and the
      eval carries on.
    - ``should_stop`` is checked *before* each case, so a stop never leaves a case half-scored.
    - ``skip_case_ids`` are not run — a resume passes the cases that already have results.

    The returned report holds only the cases this call ran, in set order.
    """
    started = _now()
    results: list[EvalCaseResult] = []
    skip = set(skip_case_ids)
    for case in eval_set.cases:
        if case.id in skip:
            continue
        if should_stop is not None:
            stop = should_stop()
            if inspect.isawaitable(stop):
                stop = await stop
            if stop:
                break
        case_result = await _score_case(runtime, eval_set, case, case_timeout_s)
        results.append(case_result)
        if on_case is not None:
            pending = on_case(case_result)
            if inspect.isawaitable(pending):
                await pending

    return EvalReport(
        eval_set_id=eval_set.metadata.id,
        target=eval_set.target,
        cases=results,
        started_at=started,
        finished_at=_now(),
    )
