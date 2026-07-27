"""CLI: ``swarmkit gates`` — pipeline gate coverage (the narrowest verified edge).

Read-only. Resolves the workspace, classifies every stage edge of a pipeline against its
funnels (:mod:`swarmkit_runtime.gate_coverage`), prints a table + a one-line verdict, and
optionally gates CI with ``--require``. Thin interface — the analysis is the shared pure
function that also backs ``GET /pipelines/{id}/gate-coverage``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from swarmkit_runtime._observability import Observability
from swarmkit_runtime.comprehension import (
    DEFAULT_FAST_APPROVE_SECONDS,
    comprehension_to_dict,
    compute_comprehension,
)
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.gate_coverage import (
    GateCoverage,
    compute_gate_coverage,
    coverage_to_dict,
)
from swarmkit_runtime.resolver import resolve_workspace

from ._app import app
from ._common import _EXIT_USAGE, _stderr

#: Exit code when a ``--require`` floor is violated (CI-gatable, like ``swarmkit eval``).
_EXIT_COVERAGE_FLOOR = 1


def _print_table(cov: GateCoverage) -> None:
    typer.echo(f"\npipeline: {cov.pipeline_id}")
    typer.echo(f"  {'stage':<22} {'gate':<12} {'pre-filters':<24} notes")
    typer.echo(f"  {'-' * 22} {'-' * 12} {'-' * 24} {'-' * 20}")
    for s in cov.stages:
        pf = ", ".join(s.pre_filters) if s.pre_filters else "—"
        notes = []
        if s.external_entry:
            notes.append("external entry")
        if s.terminal:
            notes.append("terminal")
        marker = "!" if (s.gate_class == "passthrough" and not s.terminal) else " "
        typer.echo(f"{marker} {s.stage_id:<22} {s.gate_class:<12} {pf:<24} {', '.join(notes)}")
    typer.echo(f"\n  → {cov.verdict()}")


@app.command()
def gates(
    path: Annotated[
        Path,
        typer.Argument(
            help="Workspace root (directory containing workspace.yaml).", show_default=False
        ),
    ] = Path("."),
    pipeline: Annotated[
        str | None,
        typer.Option(
            "--pipeline",
            "-p",
            help="Pipeline (StageGraph) id. Default: every pipeline.",
            show_default=False,
        ),
    ] = None,
    require: Annotated[
        str | None,
        typer.Option(
            "--require",
            help="Fail (exit 1) if any non-terminal stage edge is below this floor. "
            "Only 'human' is meaningful today: no passthrough edges allowed.",
            show_default=False,
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a human table."),
    ] = False,
) -> None:
    """Show a pipeline's gate coverage and name its narrowest verified edge."""
    workspace_root = path.resolve()
    try:
        workspace = resolve_workspace(workspace_root)
    except ResolutionErrors as exc:
        _stderr(
            f"error: workspace did not resolve ({len(exc.errors)} error(s)); "
            "run `swarmkit validate`."
        )
        raise typer.Exit(_EXIT_USAGE) from exc
    except FileNotFoundError as exc:
        _stderr(f"error: {exc}")
        raise typer.Exit(_EXIT_USAGE) from exc

    if require is not None and require != "human":
        _stderr("error: --require currently supports only 'human'.")
        raise typer.Exit(_EXIT_USAGE)

    ids = [pipeline] if pipeline else sorted(workspace.stage_graphs.keys())
    if not ids:
        _stderr("no pipelines (StageGraph artifacts) found in this workspace.")
        raise typer.Exit(_EXIT_USAGE)
    if pipeline is not None and pipeline not in workspace.stage_graphs:
        _stderr(f"error: no pipeline '{pipeline}' in this workspace.")
        raise typer.Exit(_EXIT_USAGE)

    coverages = [compute_gate_coverage(workspace, pid) for pid in ids]

    if json_output:
        typer.echo(json.dumps([coverage_to_dict(c) for c in coverages], indent=2))
    else:
        for cov in coverages:
            _print_table(cov)

    if require == "human":
        violating = [(c.pipeline_id, s) for c in coverages for s in c.violates("human")]
        if violating:
            if not json_output:
                _stderr(
                    f"\n--require human: {len(violating)} passthrough edge(s) — "
                    + ", ".join(f"{pid}:{s.stage_id}" for pid, s in violating)
                )
            raise typer.Exit(_EXIT_COVERAGE_FLOOR)


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
