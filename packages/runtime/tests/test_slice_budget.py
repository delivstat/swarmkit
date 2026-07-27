"""Slice-budget checking — slice 7 of gate-coverage-and-comprehension-debt."""

from __future__ import annotations

from swarmkit_runtime.cli import app
from swarmkit_runtime.slice_budget import check_slice_budget
from typer.testing import CliRunner

_DIFF = "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,3 @@\n a\n+b\n+c\n"  # 2 added lines, 1 file


def test_within_over_and_unbounded() -> None:
    diff = {"a.py": {1, 2, 3}, "b.py": {1}}  # 4 changed lines across 2 files
    assert check_slice_budget(diff, max_diff_lines=10, max_files=5).within_budget
    assert check_slice_budget(diff).within_budget  # no limits → always within

    over_lines = check_slice_budget(diff, max_diff_lines=3)
    assert over_lines.over_lines and not over_lines.within_budget
    assert "over slice budget" in over_lines.verdict()

    over_files = check_slice_budget(diff, max_files=1)
    assert over_files.over_files and not over_files.within_budget


def test_totals() -> None:
    r = check_slice_budget({"a.py": {1, 2, 3}, "b.py": {1}})
    assert r.total_lines == 4
    assert r.total_files == 2


def test_slice_check_cli() -> None:
    runner = CliRunner()
    over = runner.invoke(app, ["slice-check", "--max-diff-lines", "1"], input=_DIFF)
    assert over.exit_code == 1, over.output
    assert "over slice budget" in over.output

    ok = runner.invoke(app, ["slice-check", "--max-diff-lines", "10"], input=_DIFF)
    assert ok.exit_code == 0, ok.output
    assert "within budget" in ok.output
