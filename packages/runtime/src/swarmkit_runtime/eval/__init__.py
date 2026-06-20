"""Eval harness — score a topology against an eval-set (design §M15).

Run a topology over a set of cases and score each with deterministic checks + an LLM
rubric judge (a decision skill). The "test" gate of growth-through-authoring and the
"measure" signal for the fleet control plane. See design/details/eval-harness.md.
"""

from __future__ import annotations

from swarmkit_runtime.eval._checks import deterministic_checks
from swarmkit_runtime.eval._errors import (
    EvalError,
    EvalSetInvalidError,
    EvalSetNotFoundError,
)
from swarmkit_runtime.eval._loader import list_eval_sets, load_eval_set
from swarmkit_runtime.eval._models import (
    CheckResult,
    EvalCase,
    EvalCaseResult,
    EvalReport,
    EvalSet,
    ExpectSpec,
)
from swarmkit_runtime.eval._runner import run_eval_set

__all__ = [
    "CheckResult",
    "EvalCase",
    "EvalCaseResult",
    "EvalError",
    "EvalReport",
    "EvalSet",
    "EvalSetInvalidError",
    "EvalSetNotFoundError",
    "ExpectSpec",
    "deterministic_checks",
    "list_eval_sets",
    "load_eval_set",
    "run_eval_set",
]
