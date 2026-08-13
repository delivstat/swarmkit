"""The in-memory job model + store and the background execution helpers. A serve run becomes
a ``Job`` tracked here; ``execute_job`` runs the topology under a semaphore slot + timeout and
mirrors state into the sqlite store / canary router."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from swarmkit_runtime._workspace_runtime import RunResult, WorkspaceRuntime
from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.persistence import Store, usage_fields
from swarmkit_runtime.progress import set_progress_sink
from swarmkit_runtime.review._hitl import HITLDeferredError

from ._config import _DEFAULT_TIMEOUT_SECONDS


@dataclass
class Job:
    """In-memory representation of an async topology execution."""

    id: str
    topology: str
    #: `deferred` is a PAUSE, not an end: the run parked on a human gate, its state is
    #: checkpointed under this job's id, and it continues when the gate resolves.
    status: Literal["pending", "running", "completed", "failed", "deferred"]
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

    async def adopt(self, row: Any) -> Job:
        """Rehydrate an in-memory job from a durable row and track it.

        A run that parked before a restart, or was started by another instance, has no live object
        here — and `execute_job` mutates one. Rebuilding from the row is what lets such a run be
        resumed rather than being permanently stuck as a `deferred` row nobody can continue.
        """
        job = Job(
            id=row.id,
            topology=row.topology,
            status=row.status,
            input=row.input,
            version=getattr(row, "version", None),
            output=getattr(row, "output", None),
            error=getattr(row, "error", None),
            events=list(getattr(row, "events", []) or []),
            created_at=getattr(row, "created_at", "") or "",
            completed_at=getattr(row, "completed_at", None),
        )
        async with self._lock:
            self._jobs[job.id] = job
        return job

    def track_task(self, task: asyncio.Task[None]) -> None:
        """Keep a reference to a background task to prevent GC."""
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)


def _clear_progress_sink() -> None:
    set_progress_sink(None)


def _record_run_usage(store: Store, job_id: str, result: RunResult) -> None:
    """Persist a completed run's usage (design: runtime/usage-recording-and-cost).

    Delegates to the shared recorder. This function used to BE the implementation, and living here
    meant only `POST /run/{topology}` could reach it — every other run path hand-rolled the
    job-level half and wrote no per-model rows, so `/usage` answered for one path in four.
    """
    fields = usage_fields(result.usage, job_id, store)
    # The harness's work product. Always passed, never conditionally: an empty dict says a harness
    # ran and changed nothing, which is a different fact from NULL, and telling them apart is what
    # stops a dropped diff looking like a clean run.
    fields["diffs"] = getattr(result, "diffs", {}) or {}
    with contextlib.suppress(Exception):
        store.update_job(job_id, **fields)


async def execute_job(
    job: Job,
    rt: WorkspaceRuntime,
    max_steps: int,
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    semaphore: asyncio.Semaphore | None = None,
    canary_router: CanaryRouter | None = None,
    store: Store | None = None,
    resume: bool = False,
    labels: dict[str, str] | None = None,
) -> None:
    """Run topology in background, updating job state.

    ``resume`` continues a run that parked on a human gate, from its checkpoint, instead of starting
    a new one. It shares every surrounding concern deliberately — the semaphore slot, the timeout,
    usage recording and the deferral branch — because a resumed run can park again, and a second
    implementation would drift from the first exactly there.

    When a *semaphore* is provided the slot is held for the duration
    of execution so ``_register_job_routes`` can reject new requests
    with 429 when all slots are occupied.
    """
    job.status = "running"
    version_label = f" v{job.version}" if job.version else ""
    job.events.append(f"Job started for topology '{job.topology}'{version_label}")
    # Live progress into the SAME list the SSE endpoint already relays — a harness run used to be
    # silent for its whole duration because nothing appended between "started" and "completed".
    # `summary` only: this list goes over HTTP to anyone with serve:read, and a harness message can
    # quote a file (design/details/harness-progress-stream.md).
    set_progress_sink(lambda e: job.events.append(f"[{e.agent_id}] {e.summary}"))
    if store:
        store.update_job(job.id, status="running", events=job.events)
    try:
        if semaphore is not None:
            await semaphore.acquire()
        try:
            call = (
                rt.resume(job.topology, job.id, max_steps=max_steps)
                if resume
                else rt.run(
                    job.topology,
                    job.input,
                    max_steps=max_steps,
                    labels=labels,
                    # Key the run (and thus its persisted trace, .swarmkit/traces/<run-id>.json) by
                    # the job id, so GET /observability/runs/{job_id}/trace resolves it directly —
                    # no separate job→run_id mapping. run_id == job_id == thread_id for serve runs.
                    thread_id=job.id,
                )
            )
            result = await asyncio.wait_for(call, timeout=timeout_seconds)
            job.output = result.output
            job.status = "completed"
            job.events.append("Job completed successfully")
            if store is not None:
                _record_run_usage(store, job.id, result)
        except TimeoutError:
            job.error = f"Job timed out after {timeout_seconds}s"
            job.status = "failed"
            job.events.append(f"Job timed out after {timeout_seconds}s")
        except HITLDeferredError as exc:
            # A run parked on a human is NOT a failure, and serve used to record it as one — the
            # CLI has handled this since HITL landed and serve never learned to. The state is
            # checkpointed under `thread_id == job.id`, so the run resumes from where it stopped.
            job.error = f"awaiting review: {exc.reason}"
            job.status = "deferred"
            job.events.append(f"Deferred: {exc.reason}")
            # Usage up to the gate is not recorded here: the run raised, so there is no RunResult
            # to read it from. The trace IS written (WorkspaceRuntime finalises it on this path),
            # so the cost is recoverable from `/observability/runs/{id}/trace` — but the job row
            # will read zero until the run resumes and completes. Stated rather than faked.
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"
            job.events.append(f"Job failed: {exc}")
        finally:
            # Drop the sink with the job: a finished job must not keep its closure alive on the
            # ContextVar, and a later run in this context must not append to a completed job.
            _clear_progress_sink()
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
    store: Store | None = None,
    resume: bool = False,
    labels: dict[str, str] | None = None,
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
            resume=resume,
            labels=labels,
        )
    )
    job_store.track_task(task)
