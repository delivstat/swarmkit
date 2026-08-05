"""``swarmkit pipeline`` — dispatch + status for pipelines
(design/details/bundled-pipeline-orchestrator.md §4).

Enqueue events to the durable store (the `swarmkit orchestrator` process drives them) and read
run state - the same store + JSON the serve `/pipelines/*` endpoints use. `emit` starts/feeds a
pipeline; `sagas`/`status` list + inspect runs (searchable by correlation_id); `advance`/`skip`
are operator acts.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated

import typer

from swarmkit_runtime.orchestration import SagaState, SqlSagaStore

from ._app import pipeline_app

_PathArg = Annotated[
    Path, typer.Option("--workspace", "-w", help="Workspace root (directory with workspace.yaml).")
]


def _store(workspace: Path) -> SqlSagaStore:
    """The saga store the CONFIGURED backend names — not a hardcoded file.

    This used to be `sqlite:///{workspace}/.swarmkit/store.sqlite` unconditionally, so a workspace
    on Postgres had its CLI-dispatched pipeline events land in a local file serve never reads.
    """
    from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415

    store: SqlSagaStore = storage_for_workspace(workspace).saga_store()
    return store


@pipeline_app.command()
def emit(
    graph: Annotated[str, typer.Argument(help="Stage-graph id to start.")],
    workspace: _PathArg = Path("."),
    input_: Annotated[str, typer.Option("--input", help="Input payload (JSON or text).")] = "",
    tag: Annotated[
        str, typer.Option("--tag", help="Opaque tag (e.g. requirement id / site).")
    ] = "",
    correlation: Annotated[
        str | None, typer.Option("--correlation", help="Correlation id. Default: a fresh uuid.")
    ] = None,
) -> None:
    """Start (or feed) a pipeline run — enqueues an event the orchestrator drives."""
    cid = correlation or f"run-{uuid.uuid4().hex[:12]}"
    event = json.dumps({"kind": "start", "graph": graph, "tag": tag, "input": input_})
    _store(workspace).enqueue(cid, event)
    typer.echo(f"emitted start of {graph!r} → correlation {cid}")


@pipeline_app.command()
def sagas(
    query: Annotated[str, typer.Argument(help="Search by correlation id (substring).")] = "",
    workspace: _PathArg = Path("."),
    status: Annotated[str, typer.Option("--status", help="active | completed | all.")] = "all",
    graph: Annotated[str | None, typer.Option("--graph", help="Filter to a stage-graph.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List pipeline runs (searchable by correlation id, filterable by status/graph)."""
    runs = _store(workspace).list(status=status, graph=graph, query=query)
    if json_output:
        typer.echo(json.dumps([_summary(s) for s in runs], indent=2))
        return
    if not runs:
        typer.echo("no matching runs.")
        return
    for s in runs:
        where = s.pending_gate_stage or s.current_stage or "—"
        typer.echo(f"  {s.correlation_id}  [{s.status}]  {s.graph_id}  @ {where}  {s.tag}")


@pipeline_app.command()
def status(
    correlation_id: Annotated[str, typer.Argument(help="The run's correlation id.")],
    workspace: _PathArg = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Show one run's status, stages, and timeline."""
    saga = _store(workspace).get(correlation_id)
    if saga is None:
        typer.echo(f"no run {correlation_id!r}")
        raise typer.Exit(1)
    if json_output:
        typer.echo(json.dumps(_summary(saga), indent=2))
        return
    typer.echo(f"  {saga.correlation_id}  [{saga.status}]  graph={saga.graph_id}  tag={saga.tag}")
    typer.echo(f"  passed: {saga.passed_stages}   parked-on: {saga.pending_gate_stage or '—'}")
    for t in saga.timeline:
        typer.echo(f"    {t.at[:19]}  {t.kind:<10} {t.stage_id or ''}  {t.detail}")
    _echo_dead_letters(workspace, correlation_id)


def _echo_dead_letters(workspace: Path, correlation_id: str) -> None:
    """Surface events that gave up on this run.

    A stalled run used to be indistinguishable from a slow one — that is what made the reported
    outage take over an hour to identify. An event that has stopped being retried has to say so
    where an operator is already looking.
    """
    store = _store(workspace)
    failed = [
        e
        for e in getattr(store, "failed_events", lambda: [])()
        if e["correlation_id"] == correlation_id
    ]
    if not failed:
        return
    typer.echo("")
    typer.echo(f"  {len(failed)} event(s) DEAD-LETTERED — this run will not advance on its own:")
    for e in failed:
        typer.echo(f"    event {e['id']}  after {e['attempts']} attempt(s)  {e['last_error']}")
    typer.echo("  Re-queue with:  swarmkit pipeline retry-event <event-id>")


@pipeline_app.command("retry-event")
def retry_event(
    event_id: Annotated[int, typer.Argument(help="The dead-lettered event id (see `status`).")],
    workspace: _PathArg = Path("."),
) -> None:
    """Re-queue a dead-lettered event so the orchestrator picks it up again.

    The reported outage was recovered with hand-written SQL against `pipeline_events`, because
    there was no other route back: re-emitting is refused while a saga is active, and there is no
    gate to clear. This is that route.
    """
    store = _store(workspace)
    failed = {e["id"]: e for e in store.failed_events()}
    if event_id not in failed:
        typer.echo(
            f"no dead-lettered event {event_id}. `swarmkit pipeline status <id>` lists them."
        )
        raise typer.Exit(1)
    store.release(event_id, "")
    typer.echo(f"event {event_id} re-queued for {failed[event_id]['correlation_id']}.")
    typer.echo("The orchestrator will claim it on its next poll.")


@pipeline_app.command()
def advance(
    correlation_id: Annotated[str, typer.Argument()],
    stage: Annotated[str, typer.Argument(help="The parked stage to advance (approve its gate).")],
    workspace: _PathArg = Path("."),
) -> None:
    """Operator act: approve a parked gate so the run advances."""
    _store(workspace).enqueue(
        correlation_id, json.dumps({"kind": "gate", "approved": True, "stage": stage})
    )
    typer.echo(f"advanced {correlation_id} past {stage}")


@pipeline_app.command()
def skip(
    correlation_id: Annotated[str, typer.Argument()],
    stage: Annotated[str, typer.Argument(help="The parked stage to reject.")],
    workspace: _PathArg = Path("."),
) -> None:
    """Operator act: reject a parked gate (terminates the run)."""
    _store(workspace).enqueue(
        correlation_id, json.dumps({"kind": "gate", "approved": False, "stage": stage})
    )
    typer.echo(f"rejected {correlation_id} at {stage}")


def _summary(s: SagaState) -> dict[str, object]:
    return {
        "correlation_id": s.correlation_id,
        "graph": s.graph_id,
        "status": s.status,
        "current_stage": s.current_stage,
        "passed_stages": s.passed_stages,
        "pending_gate_stage": s.pending_gate_stage,
        "tag": s.tag,
    }
