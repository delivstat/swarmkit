"""Regression comparison — diff an eval run against the previous stored report."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from swarmkit_runtime.eval._models import EvalReport


@dataclass(frozen=True)
class ReportDiff:
    prev_pass_rate: float
    curr_pass_rate: float
    regressed: list[str] = field(default_factory=list)  # passed before, fails now
    fixed: list[str] = field(default_factory=list)  # failed before, passes now
    new: list[str] = field(default_factory=list)  # not present before

    @property
    def has_regression(self) -> bool:
        return bool(self.regressed)


def _results_dir(workspace_root: Path) -> Path:
    return workspace_root / ".swarmkit" / "eval-results"


def latest_prior_report(workspace_root: Path, eval_set_id: str) -> dict[str, object] | None:
    """The most recent stored report for an eval-set, or None. Call BEFORE writing the
    new report so it returns the previous run."""
    d = _results_dir(workspace_root)
    if not d.is_dir():
        return None
    prefix = f"{eval_set_id}-"
    files = sorted(f for f in d.glob("*.json") if f.name.startswith(prefix))
    if not files:
        return None
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def diff_report(prev: dict[str, object], curr: EvalReport) -> ReportDiff:
    """Per-case pass/fail delta between a prior stored report and the current run."""
    raw_cases = prev.get("cases", [])
    prev_cases: dict[str, bool] = {}
    if isinstance(raw_cases, list):
        for c in raw_cases:
            if isinstance(c, dict):
                prev_cases[str(c.get("case_id"))] = bool(c.get("passed"))
    curr_cases = {c.case_id: c.passed for c in curr.cases}
    regressed = sorted(c for c, ok in curr_cases.items() if prev_cases.get(c) is True and not ok)
    fixed = sorted(c for c, ok in curr_cases.items() if prev_cases.get(c) is False and ok)
    new = sorted(c for c in curr_cases if c not in prev_cases)
    raw_rate = prev.get("pass_rate", 0.0)
    return ReportDiff(
        prev_pass_rate=float(raw_rate) if isinstance(raw_rate, (int, float)) else 0.0,
        curr_pass_rate=curr.pass_rate,
        regressed=regressed,
        fixed=fixed,
        new=new,
    )
