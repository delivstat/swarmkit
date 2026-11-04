"""Eval harness — score a topology against an eval-set (design §M15).

Run a topology over a set of cases and score each with deterministic checks + an LLM
rubric judge (a decision skill). The "test" gate of growth-through-authoring and the
"measure" signal for the fleet control plane. See design/details/eval-harness.md.
"""

from __future__ import annotations

from swarmkit_runtime.eval._checks import deterministic_checks
from swarmkit_runtime.eval._compare import ReportDiff, diff_report, latest_prior_report
from swarmkit_runtime.eval._errors import (
    EvalError,
    EvalRunConflictError,
    EvalRunNotFoundError,
    EvalSetInvalidError,
    EvalSetNotFoundError,
    EvalTargetNotFoundError,
)
from swarmkit_runtime.eval._loader import (
    find_eval_set_file,
    list_eval_sets,
    load_eval_set,
    parse_eval_set,
)
from swarmkit_runtime.eval._models import (
    CheckResult,
    EvalCase,
    EvalCaseResult,
    EvalReport,
    EvalSet,
    ExpectSpec,
)
from swarmkit_runtime.eval._runner import run_eval_set
from swarmkit_runtime.eval._service import (
    HEARTBEAT_INTERVAL_S,
    STALE_AFTER_S,
    EvalService,
    EvalSetSummary,
)

__all__ = [
    "HEARTBEAT_INTERVAL_S",
    "STALE_AFTER_S",
    "CheckResult",
    "EvalCase",
    "EvalCaseResult",
    "EvalError",
    "EvalReport",
    "EvalRunConflictError",
    "EvalRunNotFoundError",
    "EvalService",
    "EvalSet",
    "EvalSetInvalidError",
    "EvalSetNotFoundError",
    "EvalSetSummary",
    "EvalTargetNotFoundError",
    "ExpectSpec",
    "ReportDiff",
    "deterministic_checks",
    "diff_report",
    "find_eval_set_file",
    "latest_prior_report",
    "list_eval_sets",
    "load_eval_set",
    "parse_eval_set",
    "run_eval_set",
]
