"""``swarmkit orchestrator`` — the bundled reference pipeline orchestrator process
(design/details/bundled-pipeline-orchestrator.md).

A separate long-running command (peer to Temporal): it opens the shared saga store, claims events
from the durable queue, drives each saga with the :class:`ReferenceController`, and executes stages
by calling serve's ``POST /pipelines/run-stage`` seam. It is the ONLY importer of the controller —
serve and the runtime core never touch it. Durable: a restart resumes mid-saga from the store.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from swarmkit_runtime.orchestration import SqlSagaStore, StageOutcome
from swarmkit_runtime.orchestration.reference import ReferenceController
from swarmkit_runtime.resolver import resolve_workspace

from ._app import app
from ._common import _stderr


def _load_graphs(workspace: Path) -> dict[str, dict[str, Any]]:
    """The workspace's resolved StageGraphs as ``id -> spec`` (controller reads ``spec.stages``)."""
    ws = resolve_workspace(workspace)
    graphs = getattr(ws, "stage_graphs", {}) or {}
    return {gid: dict(rg.spec) for gid, rg in graphs.items()}


def _http_run_stage(serve_url: str, token: str | None) -> Any:
    """A ``RunStage`` that executes a stage over serve's ``/pipelines/run-stage`` seam."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def run_stage(correlation_id: str, stage: dict[str, Any]) -> StageOutcome:
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(
                f"{serve_url.rstrip('/')}/pipelines/run-stage",
                json={"correlation_id": correlation_id, "stage": stage},
                headers=headers,
            )
            resp.raise_for_status()
            body = resp.json()
        return StageOutcome(
            status=body.get("status", "failed"),
            artifact=body.get("artifact", ""),
            detail=body.get("detail", ""),
        )

    return run_stage


async def run_drive_loop(
    controller: ReferenceController,
    store: Any,
    *,
    worker: str = "orchestrator-1",
    once: bool = False,
    poll_seconds: float = 1.0,
    sleep: Any = asyncio.sleep,
) -> int:
    """Claim + handle events until the queue drains (``once``) or forever. Returns the count done.

    Idempotent + durable: a claimed event is acked only after the controller applies it, and saga
    state is persisted per transition, so a crash re-drives from the store.
    """
    handled = 0
    while True:
        claimed = store.claim(worker)
        if claimed is None:
            if once:
                return handled
            await sleep(poll_seconds)
            continue
        event_id, correlation_id, event = claimed
        await controller.handle_event(correlation_id, event)
        store.ack(event_id)
        handled += 1


@app.command()
def orchestrator(
    workspace: Annotated[
        Path, typer.Argument(help="Workspace root (directory with workspace.yaml).")
    ],
    serve_url: Annotated[
        str, typer.Option("--serve-url", help="Base URL of the running swarmkit serve.")
    ] = "http://127.0.0.1:8000",
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Saga store URL. Default: the workspace store.sqlite."),
    ] = None,
    token: Annotated[
        str | None, typer.Option("--token", help="Serve API token (for run-stage calls).")
    ] = None,
    poll_seconds: Annotated[float, typer.Option("--poll", help="Idle poll interval (s).")] = 1.0,
) -> None:
    """Run the bundled reference orchestrator: drive queued pipeline events to completion."""
    db = database_url or f"sqlite:///{workspace / '.swarmkit' / 'store.sqlite'}"
    store = SqlSagaStore.from_url(db)
    graphs = _load_graphs(workspace)
    controller = ReferenceController(
        run_stage=_http_run_stage(serve_url, token), store=store, graphs=graphs
    )
    typer.echo(f"orchestrator: {len(graphs)} stage-graph(s); driving events from {db}")
    typer.echo(f"  run-stage → {serve_url}   (Ctrl-C to stop)")
    try:
        asyncio.run(run_drive_loop(controller, store, poll_seconds=poll_seconds))
    except KeyboardInterrupt:  # pragma: no cover
        _stderr("orchestrator stopped.")


__all__ = ["run_drive_loop"]
