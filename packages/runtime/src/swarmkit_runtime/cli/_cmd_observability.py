"""CLI commands — logs, status, why, stop, debug, ask, trace, checkpoints."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    pass

import typer

from swarmkit_runtime._workspace_runtime import (
    WorkspaceRuntime,
    resolve_authoring_provider,
)
from swarmkit_runtime.resolver import resolve_workspace

from ._app import app
from ._common import (
    _EXIT_USAGE,
    _not_implemented,
    _stderr,
)

# ---- logs ----------------------------------------------------------------


@app.command()
def logs(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
    last: Annotated[
        int,
        typer.Option("--last", "-n", help="Show last N runs."),
    ] = 1,
    topology: Annotated[
        str | None,
        typer.Option("--topology", "-t", help="Filter by topology name."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", "-r", help="Filter by run ID."),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option("--agent", "-a", help="Filter by agent ID."),
    ] = None,
    format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text (default) or markdown."),
    ] = "text",
) -> None:
    """Show events from recent topology runs.

    Reads from the AuditProvider (SQLite). Falls back to JSONL logs
    if the audit database has no events.
    Use --format markdown for a compliance-ready audit report.
    """
    import asyncio  # noqa: PLC0415

    ws_root = workspace_path.resolve()
    audit_db = ws_root / ".swarmkit" / "audit.sqlite"

    if audit_db.is_file():
        provider = WorkspaceRuntime.audit_provider_for(workspace_path)
        count = asyncio.get_event_loop().run_until_complete(provider.count())
        if count > 0:
            _logs_from_audit(provider, last=last, run_id=run_id, agent=agent, fmt=format)
            provider.close_sync()
            return
        provider.close_sync()

    _logs_from_jsonl(ws_root, last=last, topology=topology, fmt=format)


def _logs_from_audit(
    provider: Any,
    *,
    last: int,
    run_id: str | None,
    agent: str | None,
    fmt: str,
) -> None:
    """Read logs from AuditProvider (SQLite)."""
    from swarmkit_runtime.audit import SQLiteAuditProvider  # noqa: PLC0415
    from swarmkit_runtime.governance import AuditEvent  # noqa: PLC0415

    assert isinstance(provider, SQLiteAuditProvider)

    events: list[AuditEvent] = asyncio.get_event_loop().run_until_complete(
        _collect_audit_events(provider.query(run_id=run_id, agent_id=agent, limit=last * 50))
    )

    if not events:
        typer.echo("No events found in audit store.")
        return

    event_dicts = []
    for e in reversed(events):
        event_dicts.append(
            {
                "event_type": e.event_type,
                "agent_id": e.agent_id,
                "timestamp": e.timestamp.isoformat() if e.timestamp else "",
                "skill_id": e.skill_id,
                "duration_ms": e.duration_ms,
                "role": e.agent_role,
                "reason": e.policy_reason,
                **(e.payload or {}),
            }
        )

    if fmt == "markdown":
        typer.echo(_format_log_markdown("audit-store", event_dicts))
    else:
        typer.echo("\n── audit store ──")
        for evt in event_dicts:
            typer.echo(_format_log_event(evt))


async def _collect_audit_events(aiter: Any) -> list[Any]:
    """Collect an async iterator of AuditEvents into a list."""
    results: list[Any] = []
    async for item in aiter:
        results.append(item)
    return results


def _logs_from_jsonl(ws_root: Path, *, last: int, topology: str | None, fmt: str) -> None:
    """Fallback: read logs from JSONL files."""
    log_dir = ws_root / ".swarmkit" / "logs"
    if not log_dir.is_dir():
        typer.echo("No run logs found. Run a topology with `swarmkit run` first.")
        return

    log_files = sorted(log_dir.glob("*.jsonl"), reverse=True)
    if topology:
        log_files = [f for f in log_files if f.name.startswith(f"{topology}-")]
    log_files = log_files[:last]

    if not log_files:
        typer.echo("No matching run logs found.")
        return

    for log_file in reversed(log_files):
        events = [
            json.loads(line)
            for line in log_file.read_text(encoding="utf-8").strip().split("\n")
            if line
        ]
        if fmt == "markdown":
            typer.echo(_format_log_markdown(log_file.name, events))
        else:
            typer.echo(f"\n── {log_file.name} ──")
            for evt in events:
                typer.echo(_format_log_event(evt))


def _format_log_markdown(filename: str, events: list[dict[str, object]]) -> str:
    """Format a run log as a compliance-ready markdown audit report."""
    completed = [e for e in events if e.get("event_type") == "agent.completed"]
    denied = [e for e in events if "denied" in str(e.get("event_type", "")).lower()]
    fails = [e for e in events if "failed" in str(e.get("event_type", "")).lower()]
    skills = [e for e in events if e.get("event_type") == "skill.executed"]

    lines = [
        f"# Run Report: {filename}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Agents completed | {len(completed)} |",
        f"| Skills called | {len(skills)} |",
        f"| Policy denials | {len(denied)} |",
        f"| Validation failures | {len(fails)} |",
        f"| Total events | {len(events)} |",
        "",
    ]

    if completed:
        lines.extend(["## Agent Performance", "", "| Agent | Role | Duration |", "|---|---|---|"])
        for e in completed:
            lines.append(
                f"| {e.get('agent_id', '')} | {e.get('role', '')} | {e.get('duration_ms', '?')}ms |"
            )
        lines.append("")

    if denied:
        lines.extend(["## Policy Denials", ""])
        for e in denied:
            lines.append(f"- **{e.get('agent_id', '')}**: {e.get('reason', '')}")
        lines.append("")

    if fails:
        lines.extend(["## Validation Failures", ""])
        for e in fails:
            lines.append(f"- **{e.get('agent_id', '')}**: {e.get('error', '')}")
        lines.append("")

    lines.extend(["## Event Timeline", "", "| Timestamp | Agent | Event |", "|---|---|---|"])
    for e in events:
        ts = str(e.get("timestamp", ""))[:19]
        lines.append(f"| {ts} | {e.get('agent_id', '')} | {e.get('event_type', '')} |")

    return "\n".join(lines)


def _format_log_event(evt: dict[str, object]) -> str:
    agent = str(evt.get("agent_id", ""))
    etype = str(evt.get("event_type", ""))
    detail = {
        "agent.started": f"started  ({evt.get('role', '')})",
        "agent.completed": f"done     {evt.get('duration_ms', '?')}ms",
        "skill.executed": f"skill    {evt.get('skill_id', '')}",
        "policy.denied": f"DENIED   {evt.get('reason', '')}",
        "trust.denied": f"DENIED   {evt.get('reason', '')}",
        "output.validation_failed": f"FAIL     {evt.get('error', '')}",
        "output.validated": "valid",
    }.get(etype, etype)
    return f"  {agent:<24} {detail}"


# ---- status --------------------------------------------------------------


@app.command()
def status(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
    last: Annotated[
        int,
        typer.Option("--last", "-n", help="Show last N runs."),
    ] = 5,
) -> None:
    """Show recent run status at a glance.

    Reads from AuditProvider (SQLite) first, falls back to JSONL logs.
    """
    import asyncio  # noqa: PLC0415

    ws_root = workspace_path.resolve()
    audit_db = ws_root / ".swarmkit" / "audit.sqlite"

    if audit_db.is_file():
        provider = WorkspaceRuntime.audit_provider_for(workspace_path)
        count = asyncio.get_event_loop().run_until_complete(provider.count())
        if count > 0:
            _status_from_audit(provider, last=last)
            provider.close_sync()
            return
        provider.close_sync()

    _status_from_jsonl(ws_root, last=last)


def _status_from_audit(provider: Any, *, last: int) -> None:
    """Show status from AuditProvider (SQLite)."""
    from swarmkit_runtime.audit import SQLiteAuditProvider  # noqa: PLC0415
    from swarmkit_runtime.governance import AuditEvent  # noqa: PLC0415

    assert isinstance(provider, SQLiteAuditProvider)

    events: list[AuditEvent] = asyncio.get_event_loop().run_until_complete(
        _collect_audit_events(provider.query(limit=last * 50))
    )

    runs: dict[str, list[AuditEvent]] = {}
    for e in events:
        key = e.run_id or e.topology_id or "unknown"
        runs.setdefault(key, []).append(e)

    typer.echo(f"{'topology':<20} {'agents':<8} {'duration':<10} {'issues':<8} {'source'}")
    typer.echo("-" * 65)
    for idx, (run_key, run_events) in enumerate(runs.items()):
        if idx >= last:
            break
        completed = [e for e in run_events if e.event_type == "agent.completed"]
        denied = [e for e in run_events if "denied" in (e.event_type or "")]
        fails = [e for e in run_events if "failed" in (e.event_type or "")]
        total_ms = sum(e.duration_ms or 0 for e in completed)
        issues = len(denied) + len(fails)
        topo = run_events[0].topology_id or run_key
        typer.echo(f"{topo:<20} {len(completed):<8} {total_ms:>6}ms   {issues:<8} {'audit'}")


def _status_from_jsonl(ws_root: Path, *, last: int) -> None:
    """Fallback: show status from JSONL files."""
    log_dir = ws_root / ".swarmkit" / "logs"
    if not log_dir.is_dir():
        typer.echo("No runs recorded yet.")
        return

    log_files = sorted(log_dir.glob("*.jsonl"), reverse=True)[:last]
    if not log_files:
        typer.echo("No runs recorded yet.")
        return

    typer.echo(f"{'topology':<20} {'agents':<8} {'duration':<10} {'issues':<8} {'when'}")
    typer.echo("-" * 65)
    for lf in log_files:
        events = [json.loads(line) for line in lf.read_text().strip().split("\n") if line]
        topo = lf.stem.rsplit("-", 1)[0]
        completed = [e for e in events if e.get("event_type") == "agent.completed"]
        denied = [e for e in events if "denied" in str(e.get("event_type", "")).lower()]
        fails = [e for e in events if "failed" in str(e.get("event_type", "")).lower()]
        total_ms = sum(int(e.get("duration_ms", 0)) for e in completed)
        issues = len(denied) + len(fails)
        ts = lf.stem.rsplit("-", 1)[-1] if "-" in lf.stem else "?"
        typer.echo(f"{topo:<20} {len(completed):<8} {total_ms:>6}ms   {issues:<8} {ts}")


# ---- why -----------------------------------------------------------------


@app.command()
def why(
    run_id: Annotated[
        str,
        typer.Argument(help="Run log filename, prefix, or topology name."),
    ],
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
) -> None:
    """Explain what happened in a run using an LLM.

    Reads events from the AuditProvider (SQLite) or JSONL logs, sends
    them to the configured model provider, and returns an analysis.
    """
    events_text = _load_events_for_why(workspace_path, run_id)
    if not events_text:
        _stderr(f"No events found for '{run_id}'.")
        raise typer.Exit(_EXIT_USAGE)

    provider, model = resolve_authoring_provider()

    from swarmkit_runtime.model_providers import CompletionRequest, Message  # noqa: PLC0415

    prompt = (
        f"Analyze this SwarmKit topology execution log and explain "
        f"what happened.\n\n"
        f"Run: {run_id}\n{events_text}"
    )
    system = (
        "You are a SwarmKit run analyst. Given execution events, "
        "provide a useful analysis covering:\n"
        "1. FLOW: Which agents ran and in what order\n"
        "2. TIMING: Which agents took the longest and why\n"
        "3. SKILLS: What skills were called\n"
        "4. ISSUES: Any policy denials, trust failures, or validation "
        "failures — what went wrong and what to fix\n"
        "5. INSIGHT: One actionable observation\n\n"
        "Be specific with numbers (cite duration_ms). "
        "Be concise — 5-8 sentences. Interpret, don't just describe."
    )
    result = asyncio.run(
        provider.complete(
            CompletionRequest(
                model=model,
                messages=(Message(role="user", content=prompt),),
                system=system,
            )
        )
    )
    typer.echo(result.text or "(no analysis)")


def _load_events_for_why(workspace_path: Path, run_id: str) -> str:
    """Load events as text for the why command. Tries audit store first."""
    from swarmkit_runtime.governance import AuditEvent  # noqa: PLC0415

    ws_root = workspace_path.resolve()
    audit_db = ws_root / ".swarmkit" / "audit.sqlite"

    if audit_db.is_file():
        audit_provider = WorkspaceRuntime.audit_provider_for(workspace_path)
        events: list[AuditEvent] = asyncio.get_event_loop().run_until_complete(
            _collect_audit_events(audit_provider.query(limit=200))
        )
        audit_provider.close_sync()

        matching = [
            e
            for e in events
            if (e.topology_id and run_id in e.topology_id) or (e.run_id and run_id in e.run_id)
        ]
        if matching:
            lines = []
            for e in matching:
                lines.append(
                    json.dumps(
                        {
                            "event_type": e.event_type,
                            "agent_id": e.agent_id,
                            "timestamp": e.timestamp.isoformat() if e.timestamp else "",
                            "duration_ms": e.duration_ms,
                            "role": e.agent_role,
                            **(e.payload or {}),
                        }
                    )
                )
            return "\n".join(lines)

    traces_dir = ws_root / ".swarmkit" / "traces"
    if traces_dir.is_dir():
        matches = list(traces_dir.glob(f"{run_id}*.json"))
        if matches:
            from swarmkit_runtime.trace import RunTrace  # noqa: PLC0415

            trace = RunTrace.load(matches[0])
            return trace.render_text()

    log_dir = ws_root / ".swarmkit" / "logs"
    if log_dir.is_dir():
        matches = [
            f
            for f in log_dir.glob("*.jsonl")
            if f.name.startswith(run_id) or f.stem.startswith(run_id)
        ]
        if matches:
            return sorted(matches, reverse=True)[0].read_text(encoding="utf-8").strip()

    task_plan = ws_root / ".swarmkit" / "run-state" / "current" / "tasks.json"
    if task_plan.is_file():
        return task_plan.read_text(encoding="utf-8").strip()

    return ""


# ---- stop ----------------------------------------------------------------


@app.command()
def stop(
    run_id: Annotated[
        str,
        typer.Argument(help="Run ID to stop."),
    ],
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
) -> None:
    """Gracefully stop a running topology.

    Requests the runtime to checkpoint state and abort the current run.
    The run can be resumed later with `swarmkit run --resume`.
    """
    _not_implemented("stop", milestone="M6 (persistent mode integration)")


# ---- debug ---------------------------------------------------------------


@app.command()
def debug(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
    span_id: Annotated[
        str | None,
        typer.Option("--span-id", "-s", help="Retrieve prompt/response for a specific OTel span."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", "-r", help="Retrieve all prompts for a run."),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option("--agent", "-a", help="Retrieve recent prompts for an agent."),
    ] = None,
    last: Annotated[
        int,
        typer.Option("--last", "-n", help="Number of recent entries (with --agent)."),
    ] = 5,
) -> None:
    """Retrieve LLM prompts and responses from the local ring buffer.

    Prompts are stored locally in .swarmkit/prompts.sqlite and never
    sent to the telemetry backend. Use span IDs from OTel traces to
    correlate with the Rynko dashboard.
    """
    from swarmkit_runtime.telemetry import PromptRingBuffer  # noqa: PLC0415

    ws_root = workspace_path.resolve()
    db_path = ws_root / ".swarmkit" / "prompts.sqlite"

    if not db_path.is_file():
        typer.echo("No prompt ring buffer found. Run a topology first.")
        return

    buf = PromptRingBuffer(db_path=db_path)
    _debug_query(buf, span_id=span_id, run_id=run_id, agent=agent, last=last)
    buf.close()


def _debug_query(
    buf: Any,
    *,
    span_id: str | None,
    run_id: str | None,
    agent: str | None,
    last: int,
) -> None:
    """Dispatch debug query to the ring buffer."""
    if span_id:
        result = buf.query_by_span_id(span_id)
        if result is None:
            typer.echo(f"No prompt found for span_id '{span_id}'.")
        else:
            _print_prompt_entry(result)
        return
    if run_id:
        results = buf.query_by_run_id(run_id)
        if not results:
            typer.echo(f"No prompts found for run_id '{run_id}'.")
        else:
            for entry in results:
                _print_prompt_entry(entry)
                typer.echo("")
        return
    if agent:
        results = buf.query_by_agent(agent, last_n=last)
        if not results:
            typer.echo(f"No prompts found for agent '{agent}'.")
        else:
            for entry in results:
                _print_prompt_entry(entry)
                typer.echo("")
        return
    typer.echo(f"Prompt ring buffer: {buf.count()} entries")
    typer.echo("Use --span-id, --run-id, or --agent to query.")


def _print_prompt_entry(entry: dict[str, Any]) -> None:
    """Format and print a single prompt/response entry."""
    typer.echo(f"  span:     {entry['span_id']}")
    typer.echo(f"  run:      {entry['run_id']}")
    typer.echo(f"  agent:    {entry['agent_id']}")
    typer.echo(f"  step:     {entry['step']}")
    typer.echo(f"  model:    {entry['model']}")
    typer.echo(f"  time:     {entry['timestamp']}")
    prompt_text = entry["prompt"][:200] + ("..." if len(entry["prompt"]) > 200 else "")
    resp_text = entry["response"][:200] + ("..." if len(entry["response"]) > 200 else "")
    typer.echo(f"  prompt:   {prompt_text}")
    typer.echo(f"  response: {resp_text}")
    if entry.get("metadata"):
        typer.echo(f"  metadata: {json.dumps(entry['metadata'])}")


# ---- ask -----------------------------------------------------------------


@app.command()
def ask(
    question: Annotated[
        str,
        typer.Argument(help="Question about the workspace or recent runs."),
    ],
    workspace_path: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Workspace root."),
    ] = Path("."),
    run: Annotated[
        str | None,
        typer.Option("--run", "-r", help="Scope to a specific run ID or topology."),
    ] = None,
) -> None:
    """Ask a question about the workspace or recent runs.

    Reads structured events from the AuditProvider (SQLite) and workspace
    state, sends to an LLM for analysis. Use --run to scope to a specific
    run. Falls back to JSONL logs if no audit events.
    """
    ws_root = workspace_path.resolve()
    context_parts = _build_ask_context(ws_root, run_filter=run)

    provider, model = resolve_authoring_provider()

    from swarmkit_runtime.model_providers import CompletionRequest, Message  # noqa: PLC0415

    prompt = (
        f"Context about this SwarmKit workspace:\n\n"
        f"{chr(10).join(context_parts)}\n\n"
        f"Question: {question}"
    )
    result = asyncio.run(
        provider.complete(
            CompletionRequest(
                model=model,
                messages=(Message(role="user", content=prompt),),
                system=(
                    "You are a SwarmKit workspace assistant. You have access "
                    "to the workspace configuration and structured audit events "
                    "from recent runs.\n\n"
                    "When answering:\n"
                    "- Cite specific data: agent names, duration_ms, skill IDs\n"
                    "- If asked about performance, compare agent timings\n"
                    "- If asked about failures, explain what went wrong and "
                    "what the user can do about it\n"
                    "- If asked about configuration, reference the actual "
                    "topology/skill/archetype names from the workspace\n"
                    "- Be concise — 3-5 sentences unless the question needs more"
                ),
            )
        )
    )
    typer.echo(result.text or "(no response)")


def _build_ask_context(ws_root: Path, *, run_filter: str | None) -> list[str]:
    """Build context for the ask command from workspace + audit events."""
    from swarmkit_runtime.governance import AuditEvent  # noqa: PLC0415

    parts = ["# Workspace state"]
    try:
        workspace = resolve_workspace(ws_root)
        parts.append(f"Workspace: {workspace.raw.metadata.id}")
        parts.append(f"Topologies: {sorted(workspace.topologies.keys())}")
        parts.append(f"Skills: {sorted(workspace.skills.keys())}")
        parts.append(f"Archetypes: {sorted(workspace.archetypes.keys())}")
    except Exception:
        parts.append("(workspace could not be resolved)")

    audit_db = ws_root / ".swarmkit" / "audit.sqlite"
    if audit_db.is_file():
        audit_provider = WorkspaceRuntime.audit_provider_for(ws_root)
        events: list[AuditEvent] = asyncio.get_event_loop().run_until_complete(
            _collect_audit_events(audit_provider.query(limit=200))
        )
        audit_provider.close_sync()

        if run_filter:
            events = [
                e
                for e in events
                if (e.topology_id and run_filter in e.topology_id)
                or (e.run_id and run_filter in e.run_id)
            ]

        if events:
            parts.append("\n# Audit events (structured)")
            for e in events:
                parts.append(
                    json.dumps(
                        {
                            "event_type": e.event_type,
                            "agent_id": e.agent_id,
                            "duration_ms": e.duration_ms,
                            "role": e.agent_role,
                            "topology": e.topology_id,
                            "skill": e.skill_id,
                            "policy": e.policy_decision,
                        }
                    )
                )
            return parts

    log_dir = ws_root / ".swarmkit" / "logs"
    if log_dir.is_dir():
        recent = sorted(log_dir.glob("*.jsonl"), reverse=True)[:3]
        if recent:
            parts.append("\n# Recent run logs (JSONL fallback)")
            for lf in recent:
                parts.append(f"\n## {lf.name}")
                parts.append(lf.read_text(encoding="utf-8").strip()[:3000])

    return parts


# ---- trace ---------------------------------------------------------------


@app.command()
def trace(
    run_id: Annotated[
        str | None,
        typer.Argument(help="Run ID to display. Omit to list recent runs.", show_default=False),
    ] = None,
    workspace_path: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Workspace root directory."),
    ] = Path("."),
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Number of recent runs to list."),
    ] = 10,
) -> None:
    """Show the agent call graph and token usage for a run.

    Without arguments, lists recent runs. With a run ID, shows the
    full call graph including agent delegation, tool calls, and
    token counts per agent and model.
    """
    from swarmkit_runtime.trace import RunTrace, list_traces  # noqa: PLC0415

    ws_root = workspace_path.resolve()

    if run_id is None:
        traces = list_traces(ws_root, limit=limit)
        if not traces:
            typer.echo("No traces found. Run a topology first.")
            return
        typer.echo(f"Recent runs ({len(traces)}):\n")
        for t in traces:
            dur = t["duration_ms"] / 1000
            tokens = t["total_tokens"]
            typer.echo(
                f"  {t['run_id'][:12]}  {t['topology']:25s}  "
                f"{dur:6.1f}s  {tokens:>8,} tokens  ({t['agents']} agents)"
            )
        typer.echo(f"\nUse: swarmkit trace <run-id> -w {workspace_path}")
        return

    traces_dir = ws_root / ".swarmkit" / "traces"
    matches = list(traces_dir.glob(f"{run_id}*.json")) if traces_dir.exists() else []
    if not matches:
        _stderr(f"Trace not found: {run_id}")
        raise typer.Exit(1)

    trace_data = RunTrace.load(matches[0])
    typer.echo(trace_data.render_text())


@app.command()
def checkpoints(
    workspace_path: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Workspace root directory."),
    ] = Path("."),
) -> None:
    """List checkpointed runs that can be resumed.

    Shows the last thread ID and any available checkpoint state.
    Resume a run with: swarmkit run <workspace> <topology> --resume
    """
    ws_root = workspace_path.resolve()
    thread_file = ws_root / ".swarmkit" / "state" / "last_thread.txt"
    db_path = ws_root / ".swarmkit" / "state" / "checkpoints.db"

    if not thread_file.is_file():
        typer.echo("No checkpointed runs found.")
        return

    thread_id = thread_file.read_text(encoding="utf-8").strip()
    typer.echo(f"Last checkpointed run: {thread_id}")

    if db_path.is_file():
        import sqlite3  # noqa: PLC0415

        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT thread_id, COUNT(*) as steps "
                "FROM checkpoints GROUP BY thread_id ORDER BY rowid DESC LIMIT 10"
            ).fetchall()
            if rows:
                typer.echo(f"\nCheckpointed threads ({len(rows)}):")
                for tid, steps in rows:
                    marker = " ← resumable" if tid == thread_id else ""
                    typer.echo(f"  {tid[:16]}...  {steps} steps{marker}")
        except sqlite3.OperationalError:
            typer.echo("  (checkpoint database exists but no data)")
        finally:
            conn.close()
    else:
        typer.echo("  (no checkpoint database found)")

    typer.echo(f"\nResume: swarmkit run {workspace_path} <topology> --resume")
