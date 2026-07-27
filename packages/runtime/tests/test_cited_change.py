"""Cited-change checking — slice 5 of gate-coverage-and-comprehension-debt.

Unit tests drive the pure diff-parse + citation-coverage logic; the CLI tests run
`swarmkit cited-change` over the SDLC example fixtures (pass) and a crafted uncited
rationale (exit 1). The reference decision skill is asserted to resolve in the example.
"""

from __future__ import annotations

from pathlib import Path

from swarmkit_runtime.cited_change import (
    Citation,
    check_citations,
    parse_unified_diff,
)
from swarmkit_runtime.cli import app
from swarmkit_runtime.resolver import resolve_workspace
from typer.testing import CliRunner

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SDLC = _REPO_ROOT / "examples" / "sdlc-pipeline"
_RATIONALE = _SDLC / "fixtures" / "change-rationale.yaml"
_DIFF = _SDLC / "fixtures" / "change.diff"

_SAMPLE_DIFF = """\
--- a/src/oms/reserve.py
+++ b/src/oms/reserve.py
@@ -1,4 +1,7 @@
 def reserve(order, inventory):
-    inventory.commit(order)
+    available = inventory.available(order.sku)
+    if available < order.qty:
+        raise ReserveError(order.sku, available)
+    inventory.commit(order)
     return order
"""


def test_parse_unified_diff_tracks_new_file_lines() -> None:
    touched = parse_unified_diff(_SAMPLE_DIFF)
    assert touched == {"src/oms/reserve.py": {2, 3, 4, 5}}


def test_parse_skips_pure_deletion_targets() -> None:
    diff = "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
    assert parse_unified_diff(diff) == {}


def test_check_resolved_unresolved_and_uncovered() -> None:
    diff = parse_unified_diff(_SAMPLE_DIFF)
    cites = [
        Citation("checks stock", "src/oms/reserve.py", (2, 3)),  # resolves
        Citation("raises error", "src/oms/reserve.py", (4,)),  # resolves
        Citation("touches config", "src/oms/config.py", (1,)),  # wrong file → unresolved
    ]
    cov = check_citations(cites, diff)
    assert len(cov.resolved) == 2
    assert len(cov.unresolved) == 1
    assert cov.unresolved[0].path == "src/oms/config.py"
    assert cov.ok is False


def test_uncited_file_flagged() -> None:
    diff = parse_unified_diff(_SAMPLE_DIFF)
    # A citation to the right file but a line the diff did not change → unresolved + file uncited.
    cov = check_citations([Citation("x", "src/oms/reserve.py", (99,))], diff)
    assert cov.ok is False
    assert cov.uncovered_files == ()  # the file *is* mentioned (right path), just wrong lines
    assert len(cov.unresolved) == 1


def test_all_cited_passes() -> None:
    diff = parse_unified_diff(_SAMPLE_DIFF)
    cov = check_citations(
        [
            Citation("stock check", "src/oms/reserve.py", (2, 3)),
            Citation("raise", "src/oms/reserve.py", (4,)),
        ],
        diff,
    )
    assert cov.ok is True
    assert "every touched file is cited" in cov.verdict()


def test_cited_change_cli_passes_on_fixtures() -> None:
    result = CliRunner().invoke(
        app, ["cited-change", "--rationale", str(_RATIONALE), "--diff", str(_DIFF)]
    )
    assert result.exit_code == 0, result.output
    assert "resolve to changed lines" in result.output


def test_cited_change_cli_fails_on_uncited(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "summary: wrong\ncitations:\n  - claim: c\n    path: src/oms/reserve.py\n    lines: [99]\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app, ["cited-change", "--rationale", str(bad), "--diff", str(_DIFF)]
    )
    assert result.exit_code == 1, result.output
    assert "uncited change" in result.output


def test_reference_skill_resolves() -> None:
    ws = resolve_workspace(_SDLC / "workspace")
    assert "cited-change" in ws.skills
