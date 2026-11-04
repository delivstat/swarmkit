"""``swarmkit eval`` and ``swarmkit eval-runs`` — thin over :class:`EvalService`.

Every eval run is a row in the workspace's runtime store (design/details/eval-on-serve.md §3), the
same store serve reads, so a run started here is listed, compared against and resumed from any
front door. Before that, reports were JSON files under ``.swarmkit/eval-results/`` and a run killed
part-way through left nothing behind.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Annotated, Any

import typer

from swarmkit_runtime._workspace_runtime import MissingMCPServerError, WorkspaceRuntime
from swarmkit_runtime.errors import ResolutionErrors

from ._app import app, eval_runs_app
from ._common import _EXIT_RESOLUTION_ERROR, _EXIT_USAGE, _emit_errors, _stderr

_WORKSPACE_ARG = typer.Argument(
    help="Workspace root (containing workspace.yaml).", show_default=False
)
_SWEEP_REASON = "no process was running this eval any more (found by the sweep at startup)"


def _store(ws_root: Path) -> Any:
    """The workspace's runtime store; the local SQLite one, said out loud, when that won't open.

    A store that will not open is a reason to keep the record somewhere else, never a reason to
    lose the eval — CI gates on the exit code, not on where the row landed.
    """
    try:
        from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415

        return storage_for_workspace(ws_root).store()
    except Exception as exc:
        from swarmkit_runtime.persistence import SqliteStore  # noqa: PLC0415

        _stderr(
            f"note: the configured store did not open ({exc}); this eval is recorded in the "
            f"workspace's local store instead, under {ws_root / '.swarmkit'}."
        )
        return SqliteStore(ws_root)


def _runtime(workspace_path: Path) -> WorkspaceRuntime:
    ws_root = workspace_path.resolve()
    try:
        return WorkspaceRuntime.from_workspace_path(workspace_path)
    except ResolutionErrors as exc:
        _emit_errors(list(exc.errors), json_mode=False, workspace_root=ws_root, color=False)
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc
    except MissingMCPServerError as exc:
        for skill_id, server_id in exc.missing:
            _stderr(f"error: skill '{skill_id}' targets unconfigured MCP server '{server_id}'.")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc


def _service(workspace_path: Path, *, with_runtime: bool) -> Any:
    from swarmkit_runtime.eval import EvalService  # noqa: PLC0415

    ws_root = workspace_path.resolve()
    runtime: Any = _runtime(workspace_path) if with_runtime else None
    service = EvalService(runtime, _store(ws_root), ws_root)
    try:
        swept = service.sweep_stale(_SWEEP_REASON)
    except Exception as exc:  # a sweep that fails costs the cleanup, never the command
        _stderr(f"note: could not check for eval runs left running by a dead process: {exc}")
    else:
        if swept:
            _stderr(
                f"note: marked {swept} eval run(s) interrupted — left running by a process that "
                "is gone. `swarmkit eval-runs list` shows them; `eval-runs resume` continues one."
            )
    return service


def _case_printer(quiet: bool) -> Any:
    def _on_case(c: Any) -> None:
        if quiet:
            return
        typer.echo(f"  [{'PASS' if c.passed else 'FAIL'}] {c.case_id}")
        if not c.passed:
            for ck in c.checks:
                if not ck.passed:
                    typer.echo(f"         ✗ {ck.name}: {ck.detail}")
            if c.error:
                typer.echo(f"         ✗ error: {c.error}")

    return _on_case


def _print_outcome(ws: Path, row: Any, *, compare: bool) -> None:
    rate = row.passed / row.total if row.total else 0.0
    cost = f" · ${row.cost_usd:.4f}" if row.cost_usd else ""
    recorded = len(row.cases)
    progress = f"{row.passed}/{row.total} passed ({rate:.0%})"
    if recorded < row.total:
        progress = f"{row.passed}/{recorded} passed, {row.total - recorded} not run"
    typer.echo(f"{progress} · {row.status}{cost} · eval run: {row.id}")
    if row.error:
        typer.echo(f"error: {row.error}")
    if row.status in ("stopped", "interrupted"):
        typer.echo(f"resume: swarmkit eval-runs resume {ws} {row.id}")
    if compare:
        d = row.diff
        if d is None:
            typer.echo("compare: no prior run to compare against.")
        else:
            typer.echo(
                f"compare: pass rate {d['prev_pass_rate']:.0%} → {d['curr_pass_rate']:.0%}"
                + (f" · regressed: {', '.join(d['regressed'])}" if d["regressed"] else "")
                + (f" · fixed: {', '.join(d['fixed'])}" if d["fixed"] else "")
                + (f" · new: {', '.join(d['new'])}" if d["new"] else "")
            )


def _exit_for(row: Any) -> None:
    if row.status != "succeeded":
        raise typer.Exit(_EXIT_RESOLUTION_ERROR)


def _drive(ws: Path, run_id: str, coro: Any) -> Any:
    """Run the eval; on Ctrl-C the service marks the row interrupted, and this says how to go on."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt as exc:
        _stderr(
            f"\ninterrupted — the cases recorded so far are kept. "
            f"Resume: swarmkit eval-runs resume {ws} {run_id}"
        )
        raise typer.Exit(130) from exc


@app.command(name="eval")
def eval_(
    workspace_path: Annotated[Path, _WORKSPACE_ARG],
    eval_set: Annotated[
        str,
        typer.Argument(help="Eval-set id (under evals/) or a path to an eval-set YAML."),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Also write the JSON report here."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Only print the summary line."),
    ] = False,
    compare: Annotated[
        bool,
        typer.Option("--compare", help="Diff against the previous run (regressions/fixes)."),
    ] = False,
    case_timeout: Annotated[
        float | None,
        typer.Option(
            "--case-timeout",
            help="Fail a case that runs longer than this many seconds, and carry on.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Run an eval-set and score the topology (design §M15).

    The run is recorded in the workspace's store — `swarmkit eval-runs` lists, shows and resumes
    it. Exit code 0 if every case passes, 1 if any case fails — so CI can gate on it.
    """
    from swarmkit_runtime.eval import EvalError, EvalTargetNotFoundError  # noqa: PLC0415

    service = _service(workspace_path, with_runtime=True)
    try:
        spec = service.get_set(eval_set)
    except EvalError as exc:
        _stderr(f"error: {exc}")
        raise typer.Exit(_EXIT_USAGE) from exc

    if not quiet:
        typer.echo(f"eval: {spec.metadata.id} → topology '{spec.target}' ({len(spec.cases)} cases)")
    run_id = str(uuid.uuid4())
    try:
        row = _drive(
            workspace_path,
            run_id,
            service.run(
                eval_set,
                source="cli",
                eval_run_id=run_id,
                on_case=_case_printer(quiet),
                case_timeout_s=case_timeout,
            ),
        )
    except EvalTargetNotFoundError as exc:
        _stderr(f"error: {exc}")
        raise typer.Exit(_EXIT_USAGE) from exc

    if output is not None:
        output.write_text(json.dumps(row.report, indent=2), encoding="utf-8")
    _print_outcome(workspace_path, row, compare=compare)
    _exit_for(row)


@eval_runs_app.command("list")
def eval_runs_list(
    workspace_path: Annotated[Path, _WORKSPACE_ARG],
    eval_set: Annotated[
        str | None, typer.Option("--set", help="Only runs of this eval-set id.")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many runs to show.")] = 20,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Recent eval runs, newest first — from the CLI and serve alike."""
    rows = _service(workspace_path, with_runtime=False).list_runs(eval_set, limit=limit)
    if as_json:
        typer.echo(json.dumps([r.summary() for r in rows], indent=2))
        return
    if not rows:
        typer.echo("No eval runs recorded yet. Run one: swarmkit eval <workspace> <eval-set>")
        return
    for r in rows:
        cost = f"  ${r.cost_usd:.4f}" if r.cost_usd else ""
        typer.echo(
            f"{r.id}  {r.started_at[:19]}  {r.eval_set_id:<24} {r.status:<11} "
            f"{r.passed}/{r.total}  {r.source}{cost}"
        )


@eval_runs_app.command("show")
def eval_runs_show(
    workspace_path: Annotated[Path, _WORKSPACE_ARG],
    eval_run_id: Annotated[str, typer.Argument(help="The eval run's id.", show_default=False)],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """One eval run: status, every recorded case with its failing checks, the diff and the cost."""
    row = _service(workspace_path, with_runtime=False).get_run(eval_run_id)
    if row is None:
        _stderr(f"error: no eval run {eval_run_id!r}.")
        raise typer.Exit(_EXIT_USAGE)
    if as_json:
        typer.echo(json.dumps(row.detail(), indent=2))
        return
    typer.echo(f"eval run {row.id} · {row.eval_set_id} → '{row.target}' · {row.source}")
    typer.echo(
        f"started {row.started_at}" + (f" · finished {row.finished_at}" if row.finished_at else "")
    )
    printer = _case_printer(quiet=False)
    from swarmkit_runtime.eval import EvalService  # noqa: PLC0415

    for case in EvalService.report_of(row).cases:
        printer(case)
    _print_outcome(workspace_path, row, compare=row.diff is not None)


@eval_runs_app.command("resume")
def eval_runs_resume(
    workspace_path: Annotated[Path, _WORKSPACE_ARG],
    eval_run_id: Annotated[str, typer.Argument(help="The eval run's id.", show_default=False)],
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only print the summary.")] = False,
    case_timeout: Annotated[
        float | None,
        typer.Option("--case-timeout", help="Per-case timeout in seconds.", show_default=False),
    ] = None,
) -> None:
    """Continue an interrupted or stopped eval run — only the cases that have no result yet."""
    from swarmkit_runtime.eval import EvalError  # noqa: PLC0415

    service = _service(workspace_path, with_runtime=True)
    try:
        row = _drive(
            workspace_path,
            eval_run_id,
            service.resume(eval_run_id, on_case=_case_printer(quiet), case_timeout_s=case_timeout),
        )
    except EvalError as exc:
        _stderr(f"error: {exc}")
        raise typer.Exit(_EXIT_USAGE) from exc
    _print_outcome(workspace_path, row, compare=row.diff is not None)
    _exit_for(row)


@eval_runs_app.command("stop")
def eval_runs_stop(
    workspace_path: Annotated[Path, _WORKSPACE_ARG],
    eval_run_id: Annotated[str, typer.Argument(help="The eval run's id.", show_default=False)],
) -> None:
    """Ask a running eval — in any process sharing this store — to stop after its current case."""
    if not _service(workspace_path, with_runtime=False).request_stop(eval_run_id):
        _stderr(f"error: eval run {eval_run_id!r} is not running.")
        raise typer.Exit(_EXIT_USAGE)
    typer.echo(f"stop requested: {eval_run_id} stops after the case it is on.")
