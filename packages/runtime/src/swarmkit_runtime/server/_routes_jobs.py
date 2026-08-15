"""Async job execution, polling, SSE streaming, and webhook-trigger endpoints.

The run/webhook handlers are thin: they read app-state (runtime, canary, store, semaphore, config)
and delegate to :class:`JobService`, mapping its :class:`ServiceError` to a status code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.persistence import Store
from swarmkit_runtime.triggers import (
    PipelineIngressError,
    PipelineSignal,
    _ingress_pipeline_event,
    extract_correlation_id,
    find_pipeline_webhook_trigger,
)
from swarmkit_runtime.triggers._pipeline_ingress import DEFAULT_CORRELATION_PATH

from ._config import ServerCfg
from ._helpers import (
    _check_pipeline_webhook_signature,
    _check_webhook_signature,
    _get_runtime,
)
from ._jobs import Job, JobStore
from ._schemas import (
    JobListItem,
    JobResponse,
    RunRequest,
)
from ._services import JobService, ServiceError

logger = logging.getLogger("swarmkit.server")


class PipelineSignalDelivery(BaseModel):
    """One ``(pipeline, correlation_id, event)`` a webhook delivered to the ingress front door."""

    pipeline: str
    correlation_id: str
    event: str


class PipelineWebhookResponse(BaseModel):
    """Acknowledgement that a signed pipeline webhook was authorised, audited, and delivered.

    Returned by ``POST /hooks/{trigger_id}`` when the trigger targets a pipeline event rather than
    a topology — the emitted signals are the trigger's *declared* events only (a webhook can never
    choose the event or advance/skip a stage; design/details/pipeline-triggering.md)."""

    delivered: bool
    trigger: str
    source: str
    signals: list[PipelineSignalDelivery]


def _app_state_run_deps(
    request: Request,
) -> tuple[CanaryRouter | None, Store | None, ServerCfg, asyncio.Semaphore | None]:
    """The per-request app-state a job start needs (canary router, store, config, semaphore)."""
    return (
        getattr(request.app.state, "canary_router", None),
        getattr(request.app.state, "store", None),
        getattr(request.app.state, "server_config", ServerCfg()),
        getattr(request.app.state, "job_semaphore", None),
    )


#: Step budget for a resumed run. The original request's `max_steps` is not recorded on the job, and
#: a resume continues from a checkpoint rather than starting over, so it needs a fresh allowance
#: rather than the remainder of one nobody stored.
_DEFAULT_RESUME_STEPS = 50


def _durable_job(request: Request, job_id: str) -> Any:
    """The job as the durable store has it, or None."""
    store: Store | None = getattr(request.app.state, "store", None)
    return store.get_job(job_id) if store is not None else None


#: What the in-memory `Job` can answer for. Everything else a reader asks for comes from the
#: durable row, which is the only place it exists.
_LIVE_FIELDS = frozenset(Job.__dataclass_fields__)


class _JobView:
    """One job as BOTH stores see it.

    The two rows were described as exposing "the same fields". They do not: the in-memory `Job`
    carries what changes during a run, and `JobRow` carries that plus everything only persistence
    knows — `diffs`, `labels`, `source`, `correlation_id`, usage. Resolving in-memory-first meant a
    job THIS process started never consulted the durable row, so a diff that had been written
    correctly read as absent and `/jobs/{id}/diff` 404'd against a stored 20,997-character row.

    The live object wins for the fields it has, because they change while the run is in flight; the
    row supplies the rest. A column added to `JobRow` later is served automatically, which is the
    property whose absence caused this.
    """

    def __init__(self, live: Any, row: Any) -> None:
        self._live = live
        self._row = row

    def __getattr__(self, name: str) -> Any:
        if name in _LIVE_FIELDS:
            return getattr(self._live, name)
        return getattr(self._row, name, None)


def _resolve_job(live: Any, row: Any) -> Any:
    """The best available view of a job: merged when both exist, whichever one does otherwise."""
    if live is not None and row is not None:
        return _JobView(live, row)
    return live if live is not None else row


def _diff_length(job: Any) -> int | None:
    """Total diff characters, or None when the run carried no diff out at all.

    None and 0 are different answers: 0 means a harness ran and changed nothing, None means no
    diff reached the record. Collapsing them is what let an 850-second run that edited 11 files
    report success with its work gone.
    """
    diffs = getattr(job, "diffs", None)
    if diffs is None:
        return None
    return sum(len(d) for d in diffs.values())


def _to_response(job: Any) -> JobResponse:
    """One shape for both stores — an in-memory Job and a persisted JobRow carry the same fields
    under the same names, so the client cannot tell which one answered."""
    return JobResponse(
        job_id=job.id,
        status=job.status,
        topology=job.topology,
        output=job.output,
        error=job.error,
        input=getattr(job, "input", "") or "",
        version=getattr(job, "version", None),
        created_at=getattr(job, "created_at", "") or "",
        completed_at=getattr(job, "completed_at", None),
        # Read defensively: the in-memory Job predates the durable row's columns, so a live job
        # simply has no source or cost yet. Absent stays absent rather than becoming a zero, which
        # would read as "this run was free".
        diff_length=_diff_length(job),
        source=getattr(job, "source", None),
        correlation_id=getattr(job, "correlation_id", None),
        usage_input_tokens=getattr(job, "usage_input_tokens", None),
        usage_output_tokens=getattr(job, "usage_output_tokens", None),
        usage_cost_usd=getattr(job, "usage_cost_usd", None),
    )


def _sse(generator: AsyncGenerator[str]) -> StreamingResponse:
    """An SSE response with the headers a browser EventSource needs."""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _replay(events: list[str], status: str) -> StreamingResponse:
    """Replay a finished job's recorded events and close.

    A durable job is over by definition, so there is nothing to poll for — this yields what was
    kept (often nothing, for a run recorded only by its outcome) and then the terminator the
    client waits for, rather than leaving an EventSource to fail and the page to look broken.
    """

    async def generate() -> AsyncGenerator[str]:
        for event in events:
            yield f"data: {event}\n\n"
        yield f"data: [done] status={status}\n\n"

    return _sse(generate())


def _register_job_routes(app: FastAPI, job_store: JobStore) -> None:  # noqa: PLR0915
    """Register async job execution, polling, streaming, and webhook endpoints."""
    jobs = JobService(job_store)

    @app.post("/run/{topology_name}")
    async def run_topology(topology_name: str, body: RunRequest, request: Request) -> JobResponse:
        rt = _get_runtime(request)
        canary, store, cfg, semaphore = _app_state_run_deps(request)
        try:
            job = await jobs.start(
                rt=rt,
                canary=canary,
                store=store,
                cfg=cfg,
                semaphore=semaphore,
                topology_name=topology_name,
                user_input=body.input,
                max_steps=body.max_steps,
                correlation_id=body.correlation_id,
                labels=body.labels,
                parent_job_id=body.parent_job_id,
            )
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        return JobResponse(job_id=job.id, status="running", output=None, error=None)

    @app.get("/jobs")
    async def list_jobs() -> list[JobListItem]:
        jobs = await job_store.list_all()
        return [
            JobListItem(
                job_id=j.id,
                topology=j.topology,
                version=j.version,
                status=j.status,
                created_at=j.created_at,
                completed_at=j.completed_at,
            )
            for j in jobs
        ]

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str, request: Request) -> JobResponse:
        """One job, from the in-memory store or — failing that — the durable one.

        There are two job stores and this endpoint used to read only the first. `JobStore` holds
        what THIS serve process started via `POST /run/{topology}`; the durable store holds that
        plus every `swarmkit run` (1.150.0) and every pipeline stage (1.152.0), and survives a
        restart.

        So the history table listed rows whose detail page 404'd: the row came from
        `/jobs/history`, the page fetched `/jobs/{id}`, and the two are not the same store. A CLI
        run was visible and unopenable, and so was every job from before the last restart.
        """
        found = _resolve_job(await job_store.get(job_id), _durable_job(request, job_id))
        if found is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        return _to_response(found)

    @app.post("/jobs/{job_id}/resume")
    async def resume_job(job_id: str, request: Request) -> JobResponse:
        """Continue a run that parked on a human gate.

        The state is checkpointed under `thread_id == job.id`, so resuming needs nothing but the id.
        Without this, serve could DEFER a run and never continue it: an application that started a
        run over HTTP and saw it park had no way back in, and `swarmkit run --resume` needs the
        workspace on the same machine.

        Only a `deferred` job resumes. A completed one has nothing to continue and a running one is
        already going — replying 409 rather than quietly starting a second execution against the
        same thread, which would interleave two runs on one checkpoint.
        """
        live = await job_store.get(job_id)
        row = _durable_job(request, job_id)
        found = _resolve_job(live, row)
        if found is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        if found.status != "deferred":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Job '{job_id}' is {found.status!r}, not 'deferred' — only a run parked on a "
                    f"gate can be resumed"
                ),
            )

        rt = _get_runtime(request)
        canary, store, cfg, semaphore = _app_state_run_deps(request)
        job = await jobs.resume(
            rt=rt,
            store=store,
            cfg=cfg,
            semaphore=semaphore,
            canary=canary,
            job_id=job_id,
            durable=row,
            max_steps=_DEFAULT_RESUME_STEPS,
        )
        return _to_response(job)

    @app.get("/jobs/{job_id}/diff")
    async def get_job_diff(job_id: str, request: Request) -> dict[str, Any]:
        """The unified diff a harness run produced, per agent.

        Its own endpoint rather than a field on `GET /jobs/{id}`: a diff can be megabytes and every
        job fetch would carry it. The job response holds `diff_length` so a caller can tell there
        is something here without paying for it.
        """
        found = _resolve_job(await job_store.get(job_id), _durable_job(request, job_id))
        if found is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        diffs = getattr(found, "diffs", None)
        if diffs is None:
            # 404 on the DIFF, not on the job: this run carried none out. Distinct from an empty
            # dict, which is a harness that ran and changed nothing.
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' recorded no harness diff")
        return {
            "job_id": job_id,
            "diffs": dict(diffs),
            "length": sum(len(d) for d in diffs.values()),
        }

    @app.get("/jobs/{job_id}/stream")
    async def stream_job(job_id: str, request: Request) -> StreamingResponse:
        job = await job_store.get(job_id)
        if job is None:
            # A job this process did not start — a CLI run, a pipeline stage, or anything from
            # before the last restart. There is nothing live to follow, but 404ing makes the
            # detail page look broken; replaying what was recorded and closing is the truth.
            row = _durable_job(request, job_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
            return _replay(row.events, row.status)

        async def event_generator() -> AsyncGenerator[str]:
            sent = 0
            while True:
                current_events = job.events[sent:]
                for event in current_events:
                    yield f"data: {event}\n\n"
                    sent += 1
                if job.status in ("completed", "failed"):
                    yield f"data: [done] status={job.status}\n\n"
                    break
                await asyncio.sleep(0.3)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.post("/hooks/{topology_name}")
    async def webhook_trigger(
        topology_name: str, request: Request
    ) -> JobResponse | PipelineWebhookResponse:
        # A webhook path segment resolves to either a pipeline-event trigger (by trigger id) or an
        # ordinary topology webhook (back-compat). Route to the pipeline ingress front door when the
        # trigger targets a pipeline event; otherwise start the named topology as a job.
        trigger_configs: list[dict[str, Any]] = getattr(request.app.state, "trigger_configs", [])
        pipeline_trigger = find_pipeline_webhook_trigger(trigger_configs, topology_name)
        if pipeline_trigger is not None:
            return await _handle_pipeline_webhook(request, topology_name, pipeline_trigger)

        rt = _get_runtime(request)
        canary, store, cfg, semaphore = _app_state_run_deps(request)

        # Webhook-specific: verify the HMAC signature before doing any work, then derive the
        # user input from the (JSON or raw) body.
        raw_body = await request.body()
        _check_webhook_signature(request, raw_body, topology_name)
        try:
            body_json = await request.json()
        except Exception:
            body_json = raw_body.decode(errors="replace")
        user_input = (
            body_json.get("input", str(body_json))
            if isinstance(body_json, dict)
            else str(body_json)
        )

        try:
            job = await jobs.start(
                rt=rt,
                canary=canary,
                store=store,
                cfg=cfg,
                semaphore=semaphore,
                topology_name=topology_name,
                user_input=user_input,
                max_steps=10,
            )
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        return JobResponse(job_id=job.id, status="running")


async def _handle_pipeline_webhook(
    request: Request, trigger_id: str, trigger_config: dict[str, Any]
) -> PipelineWebhookResponse:
    """Turn a signed pipeline webhook into scoped ``emit`` events on the ingress front door.

    Validate the HMAC signature → parse the JSON body → for each of the trigger's *declared*
    ``pipeline_targets``, extract the opaque ``correlation_id`` and hand ``(correlation_id, emit)``
    to the shared authorize → audit → deliver guardrail as ``mode="emit"``. A webhook is scoped to
    exactly its declared events: it can never advance/skip a stage (those are operator acts gated
    on a reserved human-identity scope) and can never choose a different event — a body that asks
    for a non-``emit`` mode or an undeclared event is a 403 (design/details/pipeline-triggering.md
    §"The governance guardrail")."""
    raw_body = await request.body()
    _check_pipeline_webhook_signature(request, raw_body, trigger_config)
    try:
        parsed = await request.json()
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Pipeline webhook body must be a JSON object")
    body_json: dict[str, Any] = parsed

    pipeline_targets: list[dict[str, Any]] = trigger_config.get("pipeline_targets") or []
    declared_events = {str(pt.get("emit")) for pt in pipeline_targets}

    # Scoped emission: the webhook may not smuggle in a different event or an operator mode.
    requested_mode = body_json.get("mode")
    if requested_mode is not None and requested_mode != "emit":
        raise HTTPException(
            status_code=403,
            detail=(
                f"webhook {trigger_id!r} may only emit its declared pipeline event "
                f"(mode={requested_mode!r} is an operator act, never a webhook capability)"
            ),
        )
    requested_event = body_json.get("event") or body_json.get("emit")
    if requested_event is not None and requested_event not in declared_events:
        raise HTTPException(
            status_code=403,
            detail=(
                f"webhook {trigger_id!r} may only emit {sorted(declared_events)}; "
                f"it is not authorised to emit {requested_event!r}"
            ),
        )

    runtime = _get_runtime(request)
    signal: PipelineSignal | None = getattr(request.app.state, "pipeline_signal", None)
    source = f"webhook:{trigger_id}"
    source_event_id = body_json.get("source_event_id")

    # Forward the (HMAC-verified) webhook body as the pipeline input, keeping the declared `emit`
    # name for routing. The bundled controller reads this `{"kind":"event","name","input"}` envelope
    # (a bare name still works — this just also carries the payload the run is triggered with).
    body_str = raw_body.decode("utf-8", errors="replace")

    signals: list[PipelineSignalDelivery] = []
    for pt in pipeline_targets:
        emit_name = str(pt.get("emit"))
        event = json.dumps({"kind": "event", "name": emit_name, "input": body_str})
        path = pt.get("correlation_id") or DEFAULT_CORRELATION_PATH
        correlation_id = extract_correlation_id(body_json, str(path))
        if correlation_id is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"could not extract correlation_id from the webhook body via {path!r} "
                    f"(trigger {trigger_id!r})"
                ),
            )
        try:
            await _ingress_pipeline_event(
                governance=runtime.governance,
                signal=signal,
                correlation_id=correlation_id,
                event=event,
                mode="emit",
                actor_identity=source,
                source=source,
                source_event_id=source_event_id,
            )
        except PipelineIngressError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        signals.append(
            PipelineSignalDelivery(
                pipeline=str(pt.get("pipeline")),
                correlation_id=correlation_id,
                event=emit_name,  # the response reports the human-meaningful event name
            )
        )

    return PipelineWebhookResponse(
        delivered=True, trigger=trigger_id, source=source, signals=signals
    )
