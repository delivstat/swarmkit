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
from swarmkit_runtime.review._hitl import HITLDeferredError, RunStoppedError

from ._config import _DEFAULT_TIMEOUT_SECONDS


@dataclass
class Job:
    """In-memory representation of an async topology execution."""

    id: str
    topology: str
    #: `deferred` is a PAUSE, not an end: the run parked on a human gate, its state is
    #: checkpointed under this job's id, and it continues when the gate resolves.
    #: `stopped` is its own status, not a flavour of `deferred` or `failed`: deferred means
    #: "waiting on a human decision that will arrive", and nothing went wrong here.
    status: Literal[
        "queued", "pending", "running", "completed", "failed", "deferred", "stopped", "interrupted"
    ]
    input: str
    version: str | None = None
    output: str | None = None
    error: str | None = None
    events: list[str] = field(default_factory=list)
    created_at: str = ""
    completed_at: str | None = None
    #: Drift scores the run recorded (`intent.drift` events), for the canary router's criterion.
    drift_scores: list[float] = field(default_factory=list)
    #: What the caller tagged the run with (Level 16). Held here too, so the response to the
    #: submit — built from this object before the durable row is read back — carries them.
    correlation_id: str | None = None
    source: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


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

    async def remove(self, job_id: str) -> None:
        """Forget an in-memory job. Used by the API tier (enqueue mode): a job it will never
        execute must not shadow the durable row a worker keeps current — otherwise GET /jobs/{id}
        would serve this process's stale ``queued`` stub forever."""
        async with self._lock:
            self._jobs.pop(job_id, None)

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
    attachments: list[Any] | None = None,
    fence_worker_id: str | None = None,
    budget_override: dict[str, Any] | None = None,
) -> None:
    """Run topology in background, updating job state.

    *fence_worker_id* (worker-execution.md) fences every durable jobs-row write to this worker's
    ownership, so a worker whose lease expired mid-run cannot clobber the row a reclaiming worker
    now owns. None (all-in-one serve) leaves writes unconditional.

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
    # Live progress into the SAME list the SSE endpoint already relays. The LISTENER bus, not the
    # progress sink: a model agent's lines ("[assistant] thinking...", "calling get-weather") go to
    # `_helpers.progress_listener` only, and a harness's ProgressEvents are bridged onto that same
    # bus by `emit_progress`. Subscribing to the sink alone — as this did — relayed harness runs and
    # left every model run silent between "started" and "completed", the mirror image of the
    # blackout the sink was added to remove. `summary` only reaches this list either way: it goes
    # over HTTP to anyone with serve:read (design/details/harness-progress-stream.md).
    from swarmkit_runtime.langgraph_compiler._helpers import progress_listener  # noqa: PLC0415

    def _relay(line: str) -> None:
        text = line.strip()
        if text:
            job.events.append(text)

    if store:
        # started_at marks when execution actually began (queue-observability.md) - set on the
        # first durable write, in both all-in-one and worker modes, so queue wait (started_at minus
        # created_at) and execution latency (completed_at minus started_at) are recoverable.
        store.update_job(
            job.id,
            status="running",
            events=job.events,
            started_at=datetime.now(UTC).isoformat(),
            fence_worker_id=fence_worker_id,
        )
    with progress_listener(_relay):
        await _execute_job_body(
            job,
            rt,
            max_steps,
            timeout_seconds=timeout_seconds,
            semaphore=semaphore,
            canary_router=canary_router,
            store=store,
            resume=resume,
            labels=labels,
            attachments=attachments,
            fence_worker_id=fence_worker_id,
            budget_override=budget_override,
        )


async def _execute_job_body(
    job: Job,
    rt: WorkspaceRuntime,
    max_steps: int,
    *,
    timeout_seconds: int,
    semaphore: asyncio.Semaphore | None,
    canary_router: CanaryRouter | None,
    store: Store | None,
    resume: bool,
    labels: dict[str, str] | None,
    attachments: list[Any] | None,
    fence_worker_id: str | None = None,
    budget_override: dict[str, Any] | None = None,
) -> None:
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
                    # Only on a fresh run: a resumed run continues from a checkpoint whose entry
                    # message was already built, so re-attaching would either duplicate the file or
                    # silently do nothing depending on where it parked.
                    attachments=attachments,
                    # Key the run (and thus its persisted trace, .swarmkit/traces/<run-id>.json) by
                    # the job id, so GET /observability/runs/{job_id}/trace resolves it directly —
                    # no separate job→run_id mapping. run_id == job_id == thread_id for serve runs.
                    thread_id=job.id,
                    budget_override=budget_override,
                )
            )
            result = await asyncio.wait_for(call, timeout=timeout_seconds)
            job.output = result.output
            job.status = "completed"
            # Drift scores this run recorded (Level 8's `intent_monitoring`), for the canary
            # router's `drift_below` criterion — which read a constant 0 before.
            job.drift_scores = [
                float(e.payload["drift_score"])
                for e in getattr(result, "events", [])
                if e.event_type == "intent.drift" and "drift_score" in e.payload
            ]
            job.events.append("Job completed successfully")
            if store is not None:
                _record_run_usage(store, job.id, result)
        except TimeoutError:
            job.error = f"Job timed out after {timeout_seconds}s"
            job.status = "failed"
            job.events.append(f"Job timed out after {timeout_seconds}s")
        except RunStoppedError as exc:
            # Before the HITLDeferredError branch, since a stop IS one — the subclass is what lets
            # every existing checkpoint-and-exit caller work unchanged while this one distinguishes
            # "a human stopped it" from "waiting on an approval".
            job.error = "stopped by request"
            job.status = "stopped"
            job.events.append(f"Stopped: {exc.reason}")
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
                fence_worker_id=fence_worker_id,
            )
        if canary_router and job.version:
            # The BASE name: a canary-routed job's topology is the qualified `hello@0.4.0`, and
            # the router keys its metrics by `hello`. Recorded under the qualified name, every
            # result was dropped and `total_runs` stayed 0 — no canary could ever promote.
            canary_router.record_result(
                job.topology.split("@")[0],
                job.version,
                success=(job.status == "completed"),
                drift_score=_mean_drift(job.drift_scores),
            )


def _mean_drift(scores: list[float] | None) -> float | None:
    return sum(scores) / len(scores) if scores else None


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
    attachments: list[Any] | None = None,
    budget_override: dict[str, Any] | None = None,
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
            attachments=attachments,
            budget_override=budget_override,
        )
    )
    job_store.track_task(task)
