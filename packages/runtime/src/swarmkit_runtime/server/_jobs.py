"""The in-memory job model + store and the background execution helpers. A serve run becomes
a ``Job`` tracked here; ``execute_job`` runs the topology under a semaphore slot + timeout and
mirrors state into the sqlite store / canary router."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.persistence import SqliteStore

from ._config import _DEFAULT_TIMEOUT_SECONDS


@dataclass
class Job:
    """In-memory representation of an async topology execution."""

    id: str
    topology: str
    status: Literal["pending", "running", "completed", "failed"]
    input: str
    version: str | None = None
    output: str | None = None
    error: str | None = None
    events: list[str] = field(default_factory=list)
    created_at: str = ""
    completed_at: str | None = None


class JobStore:
    """Thread-safe in-memory job store."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def create(self, topology: str, user_input: str) -> Job:
        job = Job(
            id=uuid4().hex[:12],
            topology=topology,
            status="pending",
            input=user_input,
            created_at=datetime.now(UTC).isoformat(),
        )
        async with self._lock:
            self._jobs[job.id] = job
        return job

    async def get(self, job_id: str) -> Job | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def list_all(self) -> list[Job]:
        async with self._lock:
            return list(self._jobs.values())

    def track_task(self, task: asyncio.Task[None]) -> None:
        """Keep a reference to a background task to prevent GC."""
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)


async def execute_job(
    job: Job,
    rt: WorkspaceRuntime,
    max_steps: int,
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    semaphore: asyncio.Semaphore | None = None,
    canary_router: CanaryRouter | None = None,
    store: SqliteStore | None = None,
) -> None:
    """Run topology in background, updating job state.

    When a *semaphore* is provided the slot is held for the duration
    of execution so ``_register_job_routes`` can reject new requests
    with 429 when all slots are occupied.
    """
    job.status = "running"
    version_label = f" v{job.version}" if job.version else ""
    job.events.append(f"Job started for topology '{job.topology}'{version_label}")
    if store:
        store.update_job(job.id, status="running", events=job.events)
    try:
        if semaphore is not None:
            await semaphore.acquire()
        try:
            result = await asyncio.wait_for(
                rt.run(
                    job.topology,
                    job.input,
                    max_steps=max_steps,
                ),
                timeout=timeout_seconds,
            )
            job.output = result.output
            job.status = "completed"
            job.events.append("Job completed successfully")
        except TimeoutError:
            job.error = f"Job timed out after {timeout_seconds}s"
            job.status = "failed"
            job.events.append(f"Job timed out after {timeout_seconds}s")
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"
            job.events.append(f"Job failed: {exc}")
        finally:
            if semaphore is not None:
                semaphore.release()
    finally:
        job.completed_at = datetime.now(UTC).isoformat()
        if store:
            store.update_job(
                job.id,
                status=job.status,
                output=job.output,
                error=job.error,
                completed_at=job.completed_at,
                events=job.events,
            )
        if canary_router and job.version:
            canary_router.record_result(
                job.topology,
                job.version,
                success=(job.status == "completed"),
            )


def _start_job(
    job_store: JobStore,
    job: Job,
    rt: WorkspaceRuntime,
    max_steps: int,
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    semaphore: asyncio.Semaphore | None = None,
    canary_router: CanaryRouter | None = None,
    store: SqliteStore | None = None,
) -> None:
    """Create a background task for a job and track it."""
    task = asyncio.create_task(
        execute_job(
            job,
            rt,
            max_steps,
            timeout_seconds=timeout_seconds,
            semaphore=semaphore,
            canary_router=canary_router,
            store=store,
        )
    )
    job_store.track_task(task)
