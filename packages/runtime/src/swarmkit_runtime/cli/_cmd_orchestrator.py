"""``swarmkit orchestrator`` — the bundled reference pipeline orchestrator process
(design/details/bundled-pipeline-orchestrator.md).

A separate long-running command (peer to Temporal): it opens the shared saga store, claims events
from the durable queue, drives each saga with the :class:`ReferenceController`, and executes stages
by calling serve's ``POST /pipelines/run-stage`` seam. It is the ONLY importer of the controller —
serve and the runtime core never touch it. Durable: a restart resumes mid-saga from the store.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from swarmkit_runtime.orchestration import SqlSagaStore, StageOutcome
from swarmkit_runtime.orchestration.reference import ReferenceController
from swarmkit_runtime.persistence._store import redacted_url
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
        # `trust_env=False`: httpx honours HTTP_PROXY even for 127.0.0.1, and routing this
        # control-plane call through an ambient proxy is never what an operator wants. A WSL
        # `autoProxy` pointing at a dead port is what killed the orchestrator in the first place.
        async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
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
            artifact_bytes=body.get("artifact_bytes"),
        )

    return run_stage


logger = logging.getLogger("swarmkit.orchestration")

#: How many times an event is handed out before it is dead-lettered. Each attempt is a real drive
#: of real work, so this is deliberately small; the point is to survive a blip, not to grind.
_DEFAULT_MAX_ATTEMPTS = 3

#: How often a running handler refreshes its claim. Comfortably inside the store's visibility
#: timeout, so a healthy worker never has its event taken from under it.
_HEARTBEAT_SECONDS = 30.0


def default_worker_name() -> str:
    """A name that identifies THIS process.

    The default used to be the literal `orchestrator-1` for every process, so two orchestrators on
    one store were indistinguishable in `claimed_by` and raced for the same events. That matters
    more now that a stale claim can be reclaimed: "whose claim is this" has to be answerable from
    the data.
    """
    return f"{socket.gethostname()}-{os.getpid()}"


def _stage_result_lookup(workspace: Path) -> Any:
    """A ``(correlation_id, stage_id, attempt) -> StageOutcome | None`` reader over the job record.

    No new state: `jobs` is already keyed `<correlation>:<stage>` (with `@n` for a rework), and the
    artifact reference is deterministic, so "waiting on X, and X completed" is answerable from what
    is already stored.

    Best-effort — a store that will not open costs the reconciliation, never the orchestrator.
    """
    from swarmkit_runtime.artifacts import artifact_ref  # noqa: PLC0415
    from swarmkit_runtime.orchestration import StageOutcome  # noqa: PLC0415
    from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415
    from swarmkit_runtime.server._pipeline_stage import stage_run_id  # noqa: PLC0415

    try:
        store = storage_for_workspace(workspace).store()
    except Exception:
        logger.warning("stage reconciliation is unavailable: the job store did not open")
        return None

    def lookup(correlation_id: str, stage_id: str, attempt: int = 1) -> Any:
        try:
            row = store.get_job(stage_run_id(correlation_id, stage_id, attempt))
        except Exception:
            return None
        if row is None or row.status != "completed":
            return None
        return StageOutcome(
            status="completed", artifact=artifact_ref(correlation_id, stage_id), detail=""
        )

    return lookup


async def _handle_with_heartbeat(
    controller: ReferenceController,
    store: Any,
    event_id: int,
    correlation_id: str,
    event: str,
) -> None:
    """Run the handler, refreshing the claim while it works.

    A stage run can legitimately outlast any visibility timeout worth setting. Keeping the claim
    alive is what lets the timeout stay short enough to recover from a dead worker quickly, without
    a long stage being stolen from a worker that is doing fine.
    """
    task = asyncio.ensure_future(controller.handle_event(correlation_id, event))
    while True:
        done, _ = await asyncio.wait({task}, timeout=_HEARTBEAT_SECONDS)
        if done:
            await task  # re-raise the handler's exception, if any
            return
        store.heartbeat(event_id)


async def run_drive_loop(
    controller: ReferenceController,
    store: Any,
    *,
    worker: str | None = None,
    once: bool = False,
    poll_seconds: float = 1.0,
    sleep: Any = asyncio.sleep,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> int:
    """Claim + handle events until the queue drains (``once``) or forever. Returns the count done.

    Idempotent + durable: a claimed event is acked only after the controller applies it, and saga
    state is persisted per transition.

    A failing event does not take the process with it. This loop used to have no error handling at
    all, so any exception from ``handle_event`` — a network blip was enough — escaped the loop and
    ``asyncio.run()``, exiting the process AFTER the event was claimed and BEFORE it was acked.
    Since ``claim`` only looked at queued rows, that event was then unreachable by any restart: the
    orchestrator polled past it forever while its saga sat `active` with a frozen `updated_at`, and
    `pipeline status` showed a normal in-progress run. Recovery meant hand-written SQL.

    So a failure now ends one of two ways, both of them recorded: back to the queue for another
    attempt, or dead-lettered as `failed` once the attempts are exhausted. Unbounded retry is not an
    option — this loop drives real work at real cost, and a deterministically-failing event would
    spin forever.
    """
    worker = worker or default_worker_name()
    handled = 0
    while True:
        claimed = store.claim(worker)
        if claimed is None:
            if once:
                return handled
            await sleep(poll_seconds)
            continue
        event_id, correlation_id, event = claimed
        try:
            await _handle_with_heartbeat(controller, store, event_id, correlation_id, event)
        except Exception as exc:
            attempts = store.attempts(event_id)
            reason = f"{type(exc).__name__}: {exc}"
            if attempts >= max_attempts:
                store.fail(event_id, reason)
                logger.error(
                    "event %s for %r DEAD-LETTERED after %d attempt(s): %s. It will not be "
                    "retried; see `swarmkit pipeline status`.",
                    event_id,
                    correlation_id,
                    attempts,
                    reason,
                )
                continue
            store.release(event_id, reason)
            logger.warning(
                "event %s for %r failed (attempt %d/%d), returned to the queue: %s",
                event_id,
                correlation_id,
                attempts,
                max_attempts,
                reason,
            )
            # Do NOT re-claim it on this pass. Releasing and immediately re-claiming would burn
            # every attempt within milliseconds, which is exactly useless for the failure this
            # exists to survive — a network outage needs the retry to happen LATER, not three
            # times in the same instant. Waiting a poll interval gives the retry real spacing
            # without needing a scheduled-availability column.
            if once:
                return handled
            await sleep(poll_seconds)
            continue
        store.ack(event_id)
        handled += 1


def _resolve_saga_store_url(workspace: Path, override: str | None) -> tuple[str, str]:
    """The saga-store URL + where it came from, matching serve's precedence.

    ``--database-url`` is the explicit override and the ONLY supported way to point the two
    processes at different stores. Otherwise: env, then ``storage.runtime``, then the workspace
    sqlite default.
    """
    if override:
        return override, "--database-url"
    from swarmkit_runtime.persistence import StoreKind, storage_for_workspace  # noqa: PLC0415

    target = storage_for_workspace(workspace).target(StoreKind.SAGA)
    return target.url, target.source


def _deprecated() -> None:
    from swarmkit_runtime.orchestration._deprecation import warn_deprecated  # noqa: PLC0415

    warn_deprecated("swarmkit orchestrator")


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
    # Resolve the store the SAME way serve does. The orchestrator used to default to the workspace
    # store.sqlite regardless of `storage.runtime`, which was harmless only while serve ignored that
    # config too — both landed on SQLite and agreed by accident. With serve honouring it, an
    # independent default is a split brain: serve queues events into one database while the
    # orchestrator polls another, no stage ever runs, and neither process warns.
    _deprecated()
    db, source = _resolve_saga_store_url(workspace, database_url)
    store = SqlSagaStore.from_url(db)
    graphs = _load_graphs(workspace)
    controller = ReferenceController(
        run_stage=_http_run_stage(serve_url, token),
        store=store,
        graphs=graphs,
        # Lets the controller ask "did this stage already finish?" before dropping a reclaimed
        # event. Without it a stage that completed while the orchestrator was down strands its
        # saga: the work is done and paid for, and the run never moves again.
        stage_result=_stage_result_lookup(workspace),
    )
    # redacted_url, not the raw one: this line lands in terminal scrollback, any redirected log
    # and CI capture. The store URL routinely carries a database password.
    typer.echo(
        f"orchestrator: {len(graphs)} stage-graph(s); driving events from {redacted_url(db)}"
    )
    typer.echo(f"  store source: {source}")
    typer.echo(f"  run-stage → {serve_url}   (Ctrl-C to stop)")
    try:
        asyncio.run(run_drive_loop(controller, store, poll_seconds=poll_seconds))
    except KeyboardInterrupt:  # pragma: no cover
        _stderr("orchestrator stopped.")


__all__ = ["run_drive_loop"]
