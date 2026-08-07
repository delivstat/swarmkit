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
from datetime import UTC, datetime
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
        idle = _stale_for(s)
        # Marked in the LIST too: an operator scanning runs should not have to open each one to
        # find the stuck ones.
        flag = (
            f"  STALLED? {_humanise(idle)}"
            if idle is not None and idle >= _STALE_AFTER_SECONDS
            else ""
        )
        typer.echo(f"  {s.correlation_id}  [{s.status}]  {s.graph_id}  @ {where}  {s.tag}{flag}")


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
    _echo_staleness(saga)


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


#: How long an ACTIVE saga may go without moving before `status` says so. Generous on purpose: a
#: harness stage legitimately runs for many minutes, and crying stall over normal work would teach
#: an operator to ignore the signal — which is worse than not having one.
_STALE_AFTER_SECONDS = 15 * 60


def _stale_for(saga: SagaState) -> float | None:
    """Seconds since an active saga last moved, or None if it is not the kind of run that can stall.

    A saga has no timeout, so "active with a frozen `updated_at`" is the only evidence that a run
    has stopped — and it was visible nowhere. The reported outage looked exactly like a healthy
    in-progress run for over an hour: every individual record said success, and the view an operator
    reaches for showed nothing unusual.

    Parked runs are excluded. A gate waits on a human, so days of no movement is the correct
    behaviour and flagging it would be noise.
    """
    if saga.status != "active" or saga.pending_gate_stage:
        return None
    stamp = saga.updated_at or saga.created_at
    if not stamp:
        return None
    try:
        moved = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if moved.tzinfo is None:
        moved = moved.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(tz=UTC) - moved).total_seconds())


def _echo_staleness(saga: SagaState) -> None:
    """Say how long an active run has been still, when that is long enough to be worth a look."""
    idle = _stale_for(saga)
    if idle is None or idle < _STALE_AFTER_SECONDS:
        return
    typer.echo("")
    typer.echo(
        f"  STALLED? this run has not moved in {_humanise(idle)} "
        f"(active, no gate, on {saga.current_stage or '—'})."
    )
    # Deliberately a question, not a verdict: a long harness stage looks identical from here, and
    # the checks below are what tell them apart.
    typer.echo("  If its stage has already finished, the orchestrator will absorb it on the next")
    typer.echo(
        "  event; otherwise check the orchestrator is running and see the dead letters above."
    )


def _humanise(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    return f"{seconds / 3600:.1f}h"


def _summary(s: SagaState) -> dict[str, object]:
    return {
        "correlation_id": s.correlation_id,
        "graph": s.graph_id,
        "status": s.status,
        "current_stage": s.current_stage,
        "passed_stages": s.passed_stages,
        "pending_gate_stage": s.pending_gate_stage,
        "tag": s.tag,
        # Machine-readable, so a fleet view or a check script can act on it rather than parsing
        # the human line. Null when the run is not the kind that can stall.
        "stale_for_seconds": _stale_for(s),
    }
