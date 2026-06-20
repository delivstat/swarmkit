"""Regression-comparison tests."""

from __future__ import annotations

import json
from pathlib import Path

from swarmkit_runtime.eval import diff_report, latest_prior_report
from swarmkit_runtime.eval._models import EvalCaseResult, EvalReport


def _report(cases: dict[str, bool]) -> EvalReport:
    return EvalReport(
        eval_set_id="t",
        target="hello",
        cases=[EvalCaseResult(case_id=cid, passed=ok) for cid, ok in cases.items()],
    )


def test_diff_regressed_fixed_new() -> None:
    prev = _report({"a": True, "b": False, "c": True}).to_dict()
    curr = _report({"a": False, "b": True, "c": True, "d": True})  # a regressed, b fixed, d new
    d = diff_report(prev, curr)
    assert d.regressed == ["a"]
    assert d.fixed == ["b"]
    assert d.new == ["d"]
    assert d.has_regression is True


def test_diff_no_regression() -> None:
    prev = _report({"a": True}).to_dict()
    curr = _report({"a": True})
    assert diff_report(prev, curr).has_regression is False


def test_latest_prior_report(tmp_path: Path) -> None:
    d = tmp_path / ".swarmkit" / "eval-results"
    d.mkdir(parents=True)
    (d / "t-20260101T000000Z.json").write_text(json.dumps({"pass_rate": 0.5, "cases": []}))
    (d / "t-20260102T000000Z.json").write_text(json.dumps({"pass_rate": 0.9, "cases": []}))
    (d / "other-20260103T000000Z.json").write_text(json.dumps({"pass_rate": 1.0, "cases": []}))
    prior = latest_prior_report(tmp_path, "t")
    assert prior is not None and prior["pass_rate"] == 0.9  # newest for "t", ignores "other"


def test_latest_prior_none(tmp_path: Path) -> None:
    assert latest_prior_report(tmp_path, "t") is None
