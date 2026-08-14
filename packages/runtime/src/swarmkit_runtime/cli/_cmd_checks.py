"""CLI: verification checks over a run's output — comprehension, citations, slice size.

These were filed beside ``swarmkit gates`` (pipeline gate coverage), which left with the bundled
sequencer: it classified the edges of a STAGE GRAPH, and there are no stages now. Everything here is
about a single run's artifact and is unaffected — `cited-change` and `slice-check` are the same
deterministic checks a funnel's `validate` layer runs, exposed for CI and for a human.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
import yaml

from swarmkit_runtime._observability import Observability
from swarmkit_runtime.cited_change import (
    check_citations,
    parse_rationale,
    parse_unified_diff,
)
from swarmkit_runtime.cited_change import (
    coverage_to_dict as citation_coverage_to_dict,
)
from swarmkit_runtime.comprehension import (
    DEFAULT_FAST_APPROVE_SECONDS,
    comprehension_to_dict,
    compute_comprehension,
)
from swarmkit_runtime.slice_budget import check_diff_text, result_to_dict

from ._app import app
from ._common import _EXIT_USAGE, _stderr


@app.command()
def comprehension(
    path: Annotated[
        Path,
        typer.Argument(
            help="Workspace root (its .swarmkit/ audit store is read).", show_default=False
        ),
    ] = Path("."),
    fast_approve_seconds: Annotated[
        float,
        typer.Option(
            "--fast-approve-seconds",
            help="Flag approvals resolved faster than this (heuristic; report-only).",
        ),
    ] = DEFAULT_FAST_APPROVE_SECONDS,
    limit: Annotated[
        int,
        typer.Option("--limit", help="How many recent audit events to scan."),
    ] = 1000,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a human summary."),
    ] = False,
) -> None:
    """Comprehension-debt signals from the audit log (read-only, never a gate)."""
    events = Observability(path.resolve()).query_audit(limit=limit)
    if events is None:
        _stderr("no audit store yet — run a topology first (nothing to assess).")
        raise typer.Exit(0)

    report = compute_comprehension(events, fast_approve_threshold_seconds=fast_approve_seconds)
    if json_output:
        typer.echo(json.dumps(comprehension_to_dict(report), indent=2))
        return

    typer.echo(f"\ncomprehension — {report.verdict()}")
    for f in report.fast_approvals:
        typer.echo(
            f"  ! fast-approve: gate '{f.gate_id}' resolved in {f.latency_seconds:.1f}s "
            f"by {f.distinct_approvers} approver(s) (run {f.run_id})"
        )
    typer.echo("  deferred signals (need more data / later slices):")
    for d in report.deferred:
        typer.echo(f"    - {d}")


@app.command(name="cited-change")
def cited_change(
    rationale: Annotated[
        Path,
        typer.Option(
            "--rationale",
            help="Change-rationale YAML: `summary` + `citations: [{claim, path, lines}]`.",
            show_default=False,
        ),
    ],
    diff: Annotated[
        Path | None,
        typer.Option(
            "--diff",
            help="Unified diff file. Omit to read the diff from stdin.",
            show_default=False,
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a human summary."),
    ] = False,
) -> None:
    """Check a change-rationale cites the code its diff changed (exit 1 if uncited)."""
    try:
        data = yaml.safe_load(rationale.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        _stderr(f"error: could not read change-rationale: {exc}")
        raise typer.Exit(_EXIT_USAGE) from exc

    citations = parse_rationale(data)
    diff_text = diff.read_text(encoding="utf-8") if diff is not None else sys.stdin.read()
    cov = check_citations(citations, parse_unified_diff(diff_text))

    if json_output:
        typer.echo(json.dumps(citation_coverage_to_dict(cov), indent=2))
    else:
        typer.echo(f"\ncited-change — {cov.verdict()}")
        for c in cov.unresolved:
            typer.echo(
                f"  ! unresolved: '{c.claim}' cites {c.path}:{list(c.lines)} (not in the diff)"
            )
        for p in cov.uncovered_files:
            typer.echo(f"  ! uncited file: {p}")

    if not cov.ok:
        raise typer.Exit(1)


@app.command(name="slice-check")
def slice_check(
    diff: Annotated[
        Path | None,
        typer.Option(
            "--diff",
            help="Unified diff file. Omit to read the diff from stdin.",
            show_default=False,
        ),
    ] = None,
    max_diff_lines: Annotated[
        int | None,
        typer.Option(
            "--max-diff-lines",
            help="Fail if the diff changes more lines than this.",
            show_default=False,
        ),
    ] = None,
    max_files: Annotated[
        int | None,
        typer.Option(
            "--max-files", help="Fail if the diff touches more files than this.", show_default=False
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a human summary."),
    ] = False,
) -> None:
    """Check a diff against a slice budget — keep slices reviewable (exit 1 if over budget)."""
    diff_text = diff.read_text(encoding="utf-8") if diff is not None else sys.stdin.read()
    result = check_diff_text(diff_text, max_diff_lines=max_diff_lines, max_files=max_files)
    if json_output:
        typer.echo(json.dumps(result_to_dict(result), indent=2))
    else:
        typer.echo(f"\nslice-check — {result.verdict()}")
    if not result.within_budget:
        raise typer.Exit(1)
