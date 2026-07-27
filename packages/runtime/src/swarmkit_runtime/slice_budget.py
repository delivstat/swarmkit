"""Slice-budget checking — keep vertical slices small enough to review (slice 7).

Slice 7 of ``design/details/gate-coverage-and-comprehension-debt.md``. The article's "100 lines at
a time, not 2000 at the end": a stage may declare a ``slice_budget`` (``max_diff_lines`` /
``max_files``); an over-budget change should route to the funnel's ``review`` layer instead of
straight to human approval. This module is the deterministic measurement — total added/changed
lines and touched files in a unified diff, against the budget. Reuses the diff parser from
:mod:`swarmkit_runtime.cited_change`. ``swarmkit slice-check`` surfaces it (exit 1 over budget).
"""

from __future__ import annotations

from dataclasses import dataclass

from swarmkit_runtime.cited_change import parse_unified_diff


@dataclass(frozen=True)
class SliceBudgetResult:
    """A diff measured against a slice budget. A ``None`` limit leaves that dimension unbounded."""

    total_lines: int
    total_files: int
    max_diff_lines: int | None
    max_files: int | None

    @property
    def over_lines(self) -> bool:
        return self.max_diff_lines is not None and self.total_lines > self.max_diff_lines

    @property
    def over_files(self) -> bool:
        return self.max_files is not None and self.total_files > self.max_files

    @property
    def within_budget(self) -> bool:
        return not self.over_lines and not self.over_files

    def verdict(self) -> str:
        if self.max_diff_lines is None and self.max_files is None:
            return (
                f"{self.total_lines} changed line(s) across {self.total_files} "
                "file(s); no budget set."
            )
        if self.within_budget:
            return (
                f"within budget: {self.total_lines} line(s) / {self.total_files} file(s) "
                f"(limits: {self.max_diff_lines or '∞'} lines, {self.max_files or '∞'} files)."
            )
        parts: list[str] = []
        if self.over_lines:
            parts.append(f"{self.total_lines} lines > {self.max_diff_lines}")
        if self.over_files:
            parts.append(f"{self.total_files} files > {self.max_files}")
        return "over slice budget: " + "; ".join(parts) + " — split it, or route to review."


def check_slice_budget(
    diff: dict[str, set[int]],
    *,
    max_diff_lines: int | None = None,
    max_files: int | None = None,
) -> SliceBudgetResult:
    """Measure a parsed diff against a slice budget. Pure + deterministic."""
    total_lines = sum(len(lines) for lines in diff.values())
    return SliceBudgetResult(total_lines, len(diff), max_diff_lines, max_files)


def check_diff_text(
    diff_text: str,
    *,
    max_diff_lines: int | None = None,
    max_files: int | None = None,
) -> SliceBudgetResult:
    """Convenience: parse a unified diff and measure it."""
    return check_slice_budget(
        parse_unified_diff(diff_text), max_diff_lines=max_diff_lines, max_files=max_files
    )


def result_to_dict(r: SliceBudgetResult) -> dict[str, object]:
    """JSON-serializable result — shared by the CLI ``--json`` output."""
    return {
        "within_budget": r.within_budget,
        "verdict": r.verdict(),
        "total_lines": r.total_lines,
        "total_files": r.total_files,
        "max_diff_lines": r.max_diff_lines,
        "max_files": r.max_files,
    }


__all__ = [
    "SliceBudgetResult",
    "check_diff_text",
    "check_slice_budget",
    "result_to_dict",
]
