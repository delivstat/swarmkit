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
