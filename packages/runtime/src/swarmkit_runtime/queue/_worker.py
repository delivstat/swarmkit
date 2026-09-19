"""The worker loop: claim queued runs from the durable queue and execute them (worker-execution.md).

A worker is a process that pulls jobs the API tier enqueued, runs each on its own event loop + DB
engine, heartbeats while it runs, and periodically reclaims runs abandoned by dead workers.
Execution reuses the exact serve path (`server._jobs.execute_job`), so a worker-run job's status,
output, usage, diff and audit are identical to an inline one — no second implementation to drift.

Postgres only (the queue requires it). A reclaimed run (`attempt > 0`) resumes from its checkpoint
rather than restarting, which is what makes reclaim safe: continue, do not double-execute.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from pathlib import Path

from swarmkit_runtime.queue._queue import PostgresJobQueue

_logger = logging.getLogger("swarmkit.worker")

_DEFAULT_LEASE_SECONDS = 60
_DEFAULT_POLL_SECONDS = 1.0
_DEFAULT_HEARTBEAT_SECONDS = 20.0


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def run_worker(
    workspace_path: Path,
    *,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    poll_seconds: float = _DEFAULT_POLL_SECONDS,
    heartbeat_seconds: float = _DEFAULT_HEARTBEAT_SECONDS,
    max_steps: int = 50,
    once: bool = False,
    should_stop: asyncio.Event | None = None,
) -> int:
    """Drain and execute queued jobs until stopped. Returns the number of jobs executed.

    ``once`` runs a single claim-execute (or returns 0 if the queue is empty) — for tests and a
    drain-and-exit mode. ``should_stop`` lets a caller signal graceful shutdown between jobs.
    """
    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415
    from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415
    from swarmkit_runtime.server._config import _parse_server_config  # noqa: PLC0415

    rt = WorkspaceRuntime.from_workspace_path(workspace_path)
    store = storage_for_workspace(workspace_path, rt.workspace.raw).store()
    queue = PostgresJobQueue(store.engine, worker_id=_worker_id())
    cfg = _parse_server_config(rt.workspace)
    executed = 0
    _logger.info("worker %s draining the queue for %s", queue._worker_id, workspace_path)

    while should_stop is None or not should_stop.is_set():
        # Return any run abandoned by a dead worker to the queue before claiming a new one.
        try:
            reclaimed = await queue.reclaim_expired()
            if reclaimed:
                _logger.info("reclaimed %d abandoned run(s): %s", len(reclaimed), reclaimed)
        except Exception:
            _logger.warning("reclaim pass failed; continuing", exc_info=True)

        job_id = await queue.claim(lease_seconds=lease_seconds)
        if job_id is None:
            if once:
                return executed
            await asyncio.sleep(poll_seconds)
            continue

        await _run_one(queue, store, rt, cfg, job_id, lease_seconds, heartbeat_seconds, max_steps)
        executed += 1
        if once:
            return executed
    return executed


async def _run_one(
    queue: PostgresJobQueue,
    store: object,
    rt: object,
    cfg: object,
    job_id: str,
    lease_seconds: int,
    heartbeat_seconds: float,
    max_steps: int,
) -> None:
    from swarmkit_runtime.server._jobs import Job, execute_job  # noqa: PLC0415

    durable = store.get_job(job_id)  # type: ignore[attr-defined]
    if durable is None:  # claimed then deleted — nothing to run
        await queue.complete(job_id, status="failed")
        return

    # A reclaimed run (attempt > 0) resumes from its checkpoint; a fresh one (0) runs. This is the
    # idempotency: a run abandoned mid-flight continues from durable state, it does not re-execute.
    resume = durable.attempt > 0
    job = Job(
        id=durable.id,
        topology=durable.topology,
        status="running",
        input=durable.input,
        version=durable.version,
        correlation_id=durable.correlation_id,
        source=durable.source,
        labels=dict(durable.labels or {}),
    )

    stop = asyncio.Event()

    async def _heartbeat() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=heartbeat_seconds)
            except TimeoutError:
                await queue.heartbeat(job_id, lease_seconds=lease_seconds)

    hb = asyncio.create_task(_heartbeat(), name=f"heartbeat-{job_id}")
    try:
        await execute_job(
            job,
            rt,  # type: ignore[arg-type]
            max_steps,
            timeout_seconds=cfg.timeout_seconds,  # type: ignore[attr-defined]
            semaphore=None,  # a worker IS the concurrency unit; the pool of workers is the limit
            store=store,  # type: ignore[arg-type]
            resume=resume,
            labels=job.labels,
        )
    finally:
        stop.set()
        await hb
        # execute_job set the terminal status on the row; drop the lease so the reaper leaves it be.
        # Fenced on ownership: if our lease expired and the reaper handed the run to another worker,
        # this no-ops and we log it — we do NOT reset the new owner's lease (which would let the
        # reaper reclaim its actively-running job).
        won = await queue.complete(job_id, status=job.status)
    if won:
        _logger.info("worker finished %s -> %s", job_id, job.status)
    else:
        _logger.warning(
            "lost lease on %s before completing (reclaimed by another worker); result discarded",
            job_id,
        )
