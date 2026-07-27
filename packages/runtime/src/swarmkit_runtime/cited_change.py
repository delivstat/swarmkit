"""Cited-change checking — the deterministic, un-gameable core of the cited-change gate.

Slice 5 of ``design/details/gate-coverage-and-comprehension-debt.md``. A change should ship a
**change-rationale** that cites the code it touches; this module answers the mechanical half —
*do the citations resolve to lines the diff actually changed, and is every touched file cited?* —
without an LLM. You cannot cite code you did not open, so citation coverage is the closest honest
proxy for "the author traced the change". The fuzzy half (does the prose match the change?) is a
``judge`` layer on top; this is the ``validate`` half.

A **change-rationale** is a small document (not a SwarmKit artifact kind — like an approval policy,
it is config carried by a gate), e.g.::

    summary: Add stock-reservation to the OMS reserve endpoint.
    citations:
      - claim: reserve() now checks available stock before committing
        path: src/oms/reserve.py
        lines: [42, 58]

``swarmkit cited-change`` runs this over a unified diff and exits non-zero on an uncited change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class Citation:
    """A claim in a change-rationale and the code location it cites."""

    claim: str
    path: str
    lines: tuple[int, ...]


@dataclass(frozen=True)
class CitationCoverage:
    """The deterministic verdict: which citations resolve, and which touched files went uncited."""

    resolved: tuple[Citation, ...]
    unresolved: tuple[Citation, ...]
    uncovered_files: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Every citation resolves and every touched file is cited."""
        return not self.unresolved and not self.uncovered_files

    def verdict(self) -> str:
        if not self.resolved and not self.unresolved:
            return "no citations to check."
        if self.ok:
            n = len(self.resolved)
            return f"all {n} citation(s) resolve to changed lines; every touched file is cited."
        parts: list[str] = []
        if self.unresolved:
            parts.append(f"{len(self.unresolved)} citation(s) cite lines the diff did not change")
        if self.uncovered_files:
            parts.append(f"{len(self.uncovered_files)} touched file(s) uncited")
        return "uncited change: " + "; ".join(parts) + "."


def parse_unified_diff(diff_text: str) -> dict[str, set[int]]:
    """Map each changed file → the set of **new-file** line numbers it added/changed.

    Only added/context-anchored new lines are tracked (deletions have no new-file line). Handles
    ``+++ b/<path>`` headers and ``@@ -a,b +c,d @@`` hunks; ``/dev/null`` targets (pure deletions)
    are skipped.
    """
    touched: dict[str, set[int]] = {}
    path: str | None = None
    new_line = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                path = None
                continue
            # strip a leading a/ or b/ prefix (git) and any trailing tab-metadata
            target = target.split("\t", 1)[0]
            path = target[2:] if target.startswith(("a/", "b/")) else target
            touched.setdefault(path, set())
            continue
        if raw.startswith("@@"):
            m = _HUNK.match(raw)
            if m:
                new_line = int(m.group(1))
            continue
        if path is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            touched[path].add(new_line)
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            pass  # deleted line — no new-file line number consumed
        else:
            new_line += 1  # context line advances the new-file cursor
    return {p: lines for p, lines in touched.items() if lines}


def check_citations(citations: list[Citation], diff: dict[str, set[int]]) -> CitationCoverage:
    """Check citations against a parsed diff. Pure + deterministic."""
    resolved: list[Citation] = []
    unresolved: list[Citation] = []
    cited_files: set[str] = set()

    for c in citations:
        changed = diff.get(c.path)
        if changed and (not c.lines or any(ln in changed for ln in c.lines)):
            resolved.append(c)
            cited_files.add(c.path)
        else:
            unresolved.append(c)
            if changed is not None:
                cited_files.add(c.path)  # right file, wrong lines — file is still "mentioned"

    uncovered = tuple(sorted(p for p in diff if p not in cited_files))
    return CitationCoverage(tuple(resolved), tuple(unresolved), uncovered)


def coverage_to_dict(cov: CitationCoverage) -> dict[str, object]:
    """JSON-serializable coverage — shared by the CLI ``--json`` output."""
    return {
        "ok": cov.ok,
        "verdict": cov.verdict(),
        "resolved": [
            {"claim": c.claim, "path": c.path, "lines": list(c.lines)} for c in cov.resolved
        ],
        "unresolved": [
            {"claim": c.claim, "path": c.path, "lines": list(c.lines)} for c in cov.unresolved
        ],
        "uncovered_files": list(cov.uncovered_files),
    }


__all__ = [
    "Citation",
    "CitationCoverage",
    "check_citations",
    "coverage_to_dict",
    "parse_unified_diff",
]
