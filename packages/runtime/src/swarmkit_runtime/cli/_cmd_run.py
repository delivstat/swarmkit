"""CLI commands — the run + eval commands and their execution / dry-run / summary helpers."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    pass

import typer

from swarmkit_runtime._workspace_runtime import (
    MissingMCPServerError,
    RunResult,
    WorkspaceRuntime,
)
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.persistence import usage_fields
from swarmkit_runtime.progress import ProgressEvent, set_progress_sink

from ._app import app
from ._common import (
    _EXIT_RESOLUTION_ERROR,
    _EXIT_USAGE,
    _emit_errors,
    _stderr,
    _suppress_noisy_logs,
)
from ._render import should_colour

# ---- run -----------------------------------------------------------------


def _parse_labels(raw: list[str] | None) -> dict[str, str]:
    """Parse repeated ``--label key=value`` into a dict.

    A malformed label fails the run rather than being dropped: a label exists to make a run findable
    later, and one silently discarded is a run that is quietly missing from whatever query the
    caller is about to trust.
    """
    out: dict[str, str] = {}
    for item in raw or []:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            _stderr(f"error: --label must be key=value, got {item!r}")
            raise typer.Exit(2)
        out[key.strip()] = value.strip()
    return out


@app.command()
def run(  # noqa: PLR0912
    workspace_path: Annotated[
        Path,
        typer.Argument(
            help="Workspace root directory (containing workspace.yaml).",
            show_default=False,
        ),
    ],
    topology_name: Annotated[str, typer.Argument(help="Name of the topology to run.")],
    correlation_id: Annotated[
        str | None,
        typer.Option(
            "--correlation-id",
            help=(
                "Group this run with others under one id (a pipeline run, a ticket, a "
                "requirement). Written to jobs.correlation_id, which was NULL for every CLI run "
                "until now — breaking the trace chain at its first link."
            ),
        ),
    ] = None,
    save_artifact: Annotated[
        bool,
        typer.Option(
            "--save-artifact",
            help=(
                "Persist this run's output to the workspace artifact store as "
                "<correlation-id>/<run-id>/output. Requires --correlation-id. Read it back with "
                "`swarmkit artifacts get`."
            ),
        ),
    ] = False,
    label: Annotated[
        list[str] | None,
        typer.Option(
            "--label",
            help=(
                "Attach key=value to this run; repeatable. Reaches jobs.labels and "
                "audit_events.labels. Opaque to SwarmKit — it carries your model, it does not "
                "impose one."
            ),
        ),
    ] = None,
    input_text: Annotated[
        str | None,
        typer.Option(
            "--input", "-i", help="User input to send to the swarm. Reads from stdin if omitted."
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Print per-agent execution summary after output."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show resolved agents and skills without executing.",
        ),
    ] = False,
    resume: Annotated[
        bool,
        typer.Option(
            "--resume",
            help="Resume a previously interrupted run from checkpoint.",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress progress output; only print final result."),
    ] = False,
    color: Annotated[bool | None, typer.Option("--color/--no-color")] = None,
) -> None:
    """One-shot execution of a topology (design §14.1)."""
    # Flag consistency first, before the workspace is even resolved: a usage error should not
    # require a valid workspace to report, and certainly should not arrive after a paid run.
    if save_artifact and not correlation_id:
        _stderr(
            "error: --save-artifact needs --correlation-id — artifacts are addressed by "
            "correlation, and one written under an invented id could not be listed."
        )
        raise typer.Exit(_EXIT_USAGE)

    _suppress_noisy_logs()
    if quiet:
        os.environ["SWARMKIT_QUIET"] = "1"
    use_colour = should_colour(sys.stdout.isatty(), color)

    try:
        runtime = WorkspaceRuntime.from_workspace_path(workspace_path)
    except ResolutionErrors as exc:
        _emit_errors(
            list(exc.errors),
            json_mode=False,
            workspace_root=workspace_path.resolve(),
            color=use_colour,
        )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc
    except MissingMCPServerError as exc:
        for skill_id, server_id in exc.missing:
            _stderr(
                f"error: skill '{skill_id}' targets MCP server '{server_id}' "
                f"but the workspace declares no such server. "
                f"Add it under `mcp_servers:` in workspace.yaml, "
                f"or change the skill's `implementation.server` to a configured server."
            )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    if dry_run:
        _print_dry_run(runtime, topology_name)
        return

    if verbose:
        os.environ["SWARMKIT_VERBOSE"] = "1"
        # Live progress while the run is in flight. A harness node used to print nothing for the
        # whole run — minutes of silence indistinguishable from a hang. The CLI prints `detail`
        # (the harness's own text) because a local terminal already holds the workspace and its
        # credentials; serve publishes summaries only (harness-progress-stream.md).
        set_progress_sink(_print_progress)

    # Parsed before anything runs: a malformed label should cost nothing.
    run_labels = _parse_labels(label)

    if resume:
        result = _execute_resume(runtime, topology_name, workspace_path)
    else:
        user_input = input_text or ""
        if not user_input and not sys.stdin.isatty():
            user_input = sys.stdin.read().strip()
        if not user_input:
            user_input = "hello"

        prev_plan = _check_previous_plan(workspace_path)
        if prev_plan:
            result = _execute_run(
                runtime,
                topology_name,
                user_input,
                workspace_path,
                previous_plan=prev_plan,
                correlation_id=correlation_id,
                labels=run_labels,
                save_artifact=save_artifact,
            )
        else:
            result = _execute_run(
                runtime,
                topology_name,
                user_input,
                workspace_path,
                correlation_id=correlation_id,
                labels=run_labels,
                save_artifact=save_artifact,
            )

    if result.output:
        typer.echo(result.output)

    _save_run_log(workspace_path.resolve(), topology_name, result)

    if verbose and result.events:
        _print_run_summary(result)


def _check_previous_plan(workspace_path: Path) -> dict | None:  # type: ignore[type-arg]
    """Check for a previous task plan from a crashed run."""
    import json  # noqa: PLC0415

    tasks_file = workspace_path.resolve() / ".swarmkit" / "run-state" / "current" / "tasks.json"
    if not tasks_file.exists():
        return None

    try:
        plan_data = json.loads(tasks_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    tasks = plan_data.get("tasks", [])
    if not tasks:
        return None

    completed = [t for t in tasks if t.get("status") == "completed"]
    pending = [t for t in tasks if t.get("status") == "pending"]
    failed = [t for t in tasks if t.get("status") == "failed"]

    if not completed:
        return None

    _stderr(f"\nFound previous run with {len(completed)}/{len(tasks)} tasks completed:")
    for t in completed:
        findings = t.get("key_findings", [])
        summary = findings[0][:80] if findings else "no summary"
        _stderr(f"  completed: {t['id']} ({t['agent']}) — {summary}")
    for t in pending:
        _stderr(f"  pending: {t['id']} ({t['agent']})")
    for t in failed:
        _stderr(f"  failed: {t['id']} ({t['agent']}) — {t.get('error', '')[:60]}")

    if sys.stdin.isatty():
        response = input("\nResume from previous plan? [Y/n] ").strip().lower()
        if response in ("n", "no"):
            import shutil  # noqa: PLC0415

            run_state = workspace_path.resolve() / ".swarmkit" / "run-state" / "current"
            shutil.rmtree(run_state, ignore_errors=True)
            return None
    return dict(plan_data)


def _job_store(workspace_path: Path) -> Any:
    """The durable store a CLI run records into, or None if it cannot be opened.

    Best-effort by design: a store that will not open is a reason to lose the RECORD of a run, never
    a reason to lose the run. The failure is reported once rather than swallowed.
    """
    try:
        from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415

        return storage_for_workspace(workspace_path).store()
    except Exception as exc:
        _stderr(f"note: this run will not appear in history — the store did not open: {exc}")
        return None


def _finish_job(
    store: Any,
    job_id: str,
    status: str,
    *,
    output: str = "",
    error: str = "",
    usage: Any = None,
    diffs: dict[str, str] | None = None,
) -> None:
    """Close out the job row. Never raises: the run already happened."""
    if store is None:
        return
    from datetime import UTC, datetime  # noqa: PLC0415

    fields: dict[str, Any] = {
        "status": status,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    if diffs is not None:
        fields["diffs"] = diffs
    if output:
        fields["output"] = output
    if error:
        fields["error"] = error
    # One recorder for both usage sinks. Hand-rolling the job-level totals here wrote no
    # `run_usage` rows, so `/usage` — the per-model cost breakdown — never saw a CLI run.
    fields.update(usage_fields(usage, job_id, store))
    try:
        store.update_job(job_id, **fields)
    except Exception as exc:
        _stderr(f"note: could not record the run's outcome: {exc}")


def _execute_run(
    runtime: WorkspaceRuntime,
    topology_name: str,
    user_input: str,
    workspace_path: Path,
    previous_plan: dict | None = None,  # type: ignore[type-arg]
    correlation_id: str | None = None,
    labels: dict[str, str] | None = None,
    save_artifact: bool = False,
) -> RunResult:
    """Execute a topology run with HITL and interrupt handling."""
    from uuid import uuid4  # noqa: PLC0415

    thread_id = str(uuid4())
    _save_thread_id(workspace_path, thread_id)

    # Record the run so it appears in history. `swarmkit run` used to leave no job row at all —
    # the only writer was serve's JobService — so a CLI run was invisible in the UI even though it
    # had produced a trace and audit events. The THREAD id is the job id on purpose: it is also the
    # trace's run_id, so the row links straight to `.swarmkit/traces/<id>.json` and to
    # `/observability/runs/<id>/trace`, which a serve-started job cannot do.
    store = _job_store(workspace_path)
    if store is not None:
        try:
            store.create_job(
                thread_id, topology_name, user_input, correlation_id, "cli", labels=labels
            )
        except Exception as exc:
            _stderr(f"note: this run will not appear in history: {exc}")
            store = None

    try:
        result = asyncio.run(
            runtime.run(
                topology_name,
                user_input,
                labels=labels,
                thread_id=thread_id,
                previous_plan=previous_plan,
            )
        )
    except KeyError as exc:
        _finish_job(store, thread_id, "failed", error=str(exc).strip("'\""))
        _stderr(str(exc).strip("'\""))
        raise typer.Exit(_EXIT_USAGE) from exc
    except KeyboardInterrupt:
        # Not "failed": the state is checkpointed and the run is resumable. StatusBadge falls back
        # to a muted pill for a word it does not know, so an honest status costs nothing.
        _finish_job(store, thread_id, "interrupted", error="interrupted; resumable with --resume")
        _stderr("\n⏸ Run interrupted. State checkpointed.")
        _stderr(f"  Resume with: swarmkit run {workspace_path} {topology_name} --resume")
        raise typer.Exit(0) from None
    except Exception as exc:
        from swarmkit_runtime.review._hitl import HITLDeferredError  # noqa: PLC0415

        if isinstance(exc, HITLDeferredError):
            _finish_job(store, thread_id, "deferred", error=f"awaiting review: {exc.reason}")
            _stderr(f"\n⏸ Review deferred: {exc.reason}")
            _stderr(f"  1. Approve: swarmkit review approve <id> {workspace_path}")
            _stderr(f"  2. Resume:  swarmkit run {workspace_path} {topology_name} --resume")
            raise typer.Exit(0) from None
        _finish_job(store, thread_id, "failed", error=f"{type(exc).__name__}: {exc}")
        _stderr(f"\nerror: execution failed: {exc}")
        _stderr("\n⏸ State may be checkpointed. Try resuming:")
        _stderr(f"  swarmkit run {workspace_path} {topology_name} --resume")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    _finish_job(
        store,
        thread_id,
        "completed",
        output=result.output or "",
        usage=result.usage,
        diffs=result.diffs,
    )
    if save_artifact:
        _persist_artifact(workspace_path, correlation_id, thread_id, result.output or "")
    return result


def _persist_artifact(
    workspace_path: Path, correlation_id: str | None, run_id: str, content: str
) -> None:
    """Write a one-shot run's output to the workspace artifact store.

    The ref is ``<correlation_id>/<run_id>/output`` — the same three-part shape a pipeline stage
    uses, with the RUN in the stage slot. A one-shot run has no stage, and the run id is the segment
    that keeps retries and re-runs under one correlation from overwriting each other. It is also the
    id the rest of the chain already uses (`jobs.id`, `audit_events.run_id`), so a reader who has
    one has the other.

    Requires a correlation id rather than inventing one: an artifact nobody can group is an artifact
    nobody finds, and `list()` addresses by correlation.
    """
    if not correlation_id:
        # Unreachable from the CLI (checked up front, before the run costs anything); kept because
        # `_execute_run` is called directly by tests and could be by future callers.
        _stderr("note: not saving an artifact: no correlation id")
        return

    from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415

    try:
        # The one resolver, not a second store: the workspace's `storage.artifacts` backend
        # (database / filesystem / s3) is the caller's choice and this must honour it.
        ref = (
            storage_for_workspace(workspace_path)
            .artifact_store()
            .put(correlation_id, run_id, content)
        )
    except Exception as exc:
        # The run itself succeeded and its output has already been printed and recorded on the job
        # row. Failing here would throw away work over a storage problem.
        _stderr(f"note: could not save the artifact: {exc}")
        return
    typer.echo(f"artifact: {ref}")


def _save_thread_id(workspace_path: Path, thread_id: str) -> None:
    """Save the thread_id so --resume can find it."""
    state_dir = workspace_path.resolve() / ".swarmkit" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "last_thread.txt").write_text(thread_id, encoding="utf-8")


def _execute_resume(
    runtime: WorkspaceRuntime,
    topology_name: str,
    workspace_path: Path,
) -> RunResult:
    """Resume a previously checkpointed run."""
    thread_file = workspace_path.resolve() / ".swarmkit" / "state" / "last_thread.txt"
    if not thread_file.is_file():
        _stderr("No checkpointed run found to resume.")
        _stderr(f"Expected at: {thread_file}")
        raise typer.Exit(_EXIT_USAGE)

    thread_id = thread_file.read_text(encoding="utf-8").strip()
    _stderr(f"Resuming from checkpoint: {thread_id}")

    try:
        return asyncio.run(runtime.resume(topology_name, thread_id))
    except Exception as exc:
        _stderr(f"error: resume failed: {exc}")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc


# ---- eval (score a topology against an eval-set) -------------------------


@app.command(name="eval")
def eval_(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root (containing workspace.yaml).", show_default=False),
    ],
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
) -> None:
    """Run an eval-set and score the topology (design §M15).

    Exit code 0 if every case passes, 1 if any case fails — so CI can gate on it.
    """
    import datetime  # noqa: PLC0415

    from swarmkit_runtime.eval import (  # noqa: PLC0415
        EvalCaseResult,
        EvalError,
        diff_report,
        latest_prior_report,
        load_eval_set,
        run_eval_set,
    )

    ws_root = workspace_path.resolve()
    try:
        runtime = WorkspaceRuntime.from_workspace_path(workspace_path)
    except ResolutionErrors as exc:
        _emit_errors(list(exc.errors), json_mode=False, workspace_root=ws_root, color=False)
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc
    except MissingMCPServerError as exc:
        for skill_id, server_id in exc.missing:
            _stderr(f"error: skill '{skill_id}' targets unconfigured MCP server '{server_id}'.")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    try:
        spec = load_eval_set(ws_root, eval_set)
    except EvalError as exc:
        _stderr(f"error: {exc}")
        raise typer.Exit(_EXIT_USAGE) from exc

    def _on_case(c: EvalCaseResult) -> None:
        if not quiet:
            mark = "PASS" if c.passed else "FAIL"
            typer.echo(f"  [{mark}] {c.case_id}")
            if not c.passed:
                for ck in c.checks:
                    if not ck.passed:
                        typer.echo(f"         ✗ {ck.name}: {ck.detail}")
                if c.error:
                    typer.echo(f"         ✗ error: {c.error}")

    # Capture the previous run BEFORE writing the new report (so it's the prior one).
    prior = latest_prior_report(ws_root, spec.metadata.id) if compare else None

    if not quiet:
        typer.echo(f"eval: {spec.metadata.id} → topology '{spec.target}' ({len(spec.cases)} cases)")
    report = asyncio.run(run_eval_set(runtime, spec, on_case=_on_case))

    report_dir = ws_root / ".swarmkit" / "eval-results"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    stored = report_dir / f"{spec.metadata.id}-{stamp}.json"
    payload = json.dumps(report.to_dict(), indent=2)
    stored.write_text(payload, encoding="utf-8")
    if output is not None:
        output.write_text(payload, encoding="utf-8")

    typer.echo(f"{report.passed}/{report.total} passed ({report.pass_rate:.0%}) · report: {stored}")

    if compare:
        if prior is None:
            typer.echo("compare: no prior run to compare against.")
        else:
            d = diff_report(prior, report)
            typer.echo(
                f"compare: pass rate {d.prev_pass_rate:.0%} → {d.curr_pass_rate:.0%}"
                + (f" · regressed: {', '.join(d.regressed)}" if d.regressed else "")
                + (f" · fixed: {', '.join(d.fixed)}" if d.fixed else "")
                + (f" · new: {', '.join(d.new)}" if d.new else "")
            )

    if report.failed:
        raise typer.Exit(_EXIT_RESOLUTION_ERROR)


# ---- dry run -------------------------------------------------------------


def _print_progress(event: ProgressEvent) -> None:
    """One line per event as it happens, with the harness's own text indented under it."""
    typer.echo(f"  · [{event.agent_id}] {event.summary}")
    if event.kind == "message" and event.detail.strip() != event.summary.strip():
        for line in event.detail.strip().splitlines():
            typer.echo(f"      {line}")


def _print_dry_run(runtime: WorkspaceRuntime, topology_name: str) -> None:
    """Show the resolved topology without executing — no LLM or MCP calls."""
    ws = runtime.workspace
    if topology_name not in ws.topologies:
        available = sorted(ws.topologies.keys())
        _stderr(f"Topology '{topology_name}' not found. Available: {available}")
        raise typer.Exit(_EXIT_USAGE)

    topology = ws.topologies[topology_name]
    typer.echo(f"── dry run: {topology_name} ──\n")
    typer.echo("Agents:")
    _print_agent_tree(topology.root, indent=2)

    mcp_ids = runtime.mcp_manager.server_ids if runtime.mcp_manager else []
    if mcp_ids:
        typer.echo(f"\nMCP servers: {', '.join(mcp_ids)}")

    gov_type = type(runtime.governance).__name__
    typer.echo(f"Governance: {gov_type}")
    typer.echo("\nNo LLM or MCP calls made. Use without --dry-run to execute.")


def _print_agent_tree(agent: object, indent: int = 0) -> None:
    prefix = " " * indent
    agent_id = getattr(agent, "id", "?")
    role = getattr(agent, "role", "?")
    model = getattr(agent, "model", None) or {}
    provider = model.get("provider", "?") if isinstance(model, dict) else "?"
    model_name = model.get("name", "?") if isinstance(model, dict) else "?"
    skills = [s.id for s in getattr(agent, "skills", ())]

    typer.echo(f"{prefix}{agent_id} ({role}) — {provider}/{model_name}")
    if skills:
        typer.echo(f"{prefix}  skills: {', '.join(skills)}")
    for child in getattr(agent, "children", ()):
        _print_agent_tree(child, indent + 4)


# ---- run observability helpers -------------------------------------------


def _print_run_summary(result: RunResult) -> None:
    """Print a per-agent execution summary from run events."""
    typer.echo("\n── run summary ──")
    completed = [e for e in result.events if e.event_type == "agent.completed"]
    denied = [e for e in result.events if e.event_type in ("policy.denied", "trust.denied")]
    skills = [e for e in result.events if e.event_type == "skill.executed"]
    validation_fails = [e for e in result.events if e.event_type == "output.validation_failed"]

    for evt in completed:
        duration = evt.payload.get("duration_ms", "?")
        role = evt.payload.get("role", "")
        typer.echo(f"  {evt.agent_id:<24} {role:<8} {duration:>6}ms")

    if skills:
        typer.echo(f"\n  skills called: {len(skills)}")
    if denied:
        typer.echo(f"  policy denials: {len(denied)}")
        for d in denied:
            typer.echo(f"    {d.agent_id}: {d.payload.get('reason', '')}")
    if validation_fails:
        typer.echo(f"  output validation failures: {len(validation_fails)}")
        for v in validation_fails:
            typer.echo(f"    {v.agent_id}: {v.payload.get('error', '')}")

    usage = result.usage
    if usage is not None and usage.compression_calls > 0 and usage.compression_bytes_in > 0:
        saved = usage.compression_bytes_in - usage.compression_bytes_out
        pct = 100 * saved / usage.compression_bytes_in
        typer.echo(
            f"  context compression: {usage.compression_bytes_in:,} -> "
            f"{usage.compression_bytes_out:,} chars "
            f"({pct:.0f}% off, {usage.compression_calls} results)"
        )

    typer.echo(f"  total events: {len(result.events)}")


def _save_run_log(ws_root: Path, topology: str, result: RunResult) -> None:
    """Save run events to .swarmkit/logs/ as JSONL for later analysis."""
    if not result.events:
        return
    from datetime import UTC, datetime  # noqa: PLC0415

    log_dir = ws_root / ".swarmkit" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    log_file = log_dir / f"{topology}-{ts}.jsonl"
    lines = []
    for evt in result.events:
        entry = {
            "event_type": evt.event_type,
            "agent_id": evt.agent_id,
            "timestamp": evt.timestamp,
            "skill_id": evt.skill_id,
            **evt.payload,
        }
        lines.append(json.dumps(entry))
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
