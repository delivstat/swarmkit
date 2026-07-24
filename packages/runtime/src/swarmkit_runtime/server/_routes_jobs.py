"""Async job execution, polling, SSE streaming, and webhook-trigger endpoints.

The run/webhook handlers are thin: they read app-state (runtime, canary, store, semaphore, config)
and delegate to :class:`JobService`, mapping its :class:`ServiceError` to a status code.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.orchestration import PipelineSignal
from swarmkit_runtime.persistence import Store
from swarmkit_runtime.triggers import extract_correlation_id, find_pipeline_webhook_trigger
from swarmkit_runtime.triggers._pipeline_ingress import DEFAULT_CORRELATION_PATH

from ._config import ServerCfg
from ._helpers import (
    _check_pipeline_webhook_signature,
    _check_webhook_signature,
    _get_runtime,
)
from ._jobs import JobStore
from ._routes_pipelines import (
    PipelineIngressError,
    _ingress_pipeline_event,
)
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


def _register_job_routes(app: FastAPI, job_store: JobStore) -> None:
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
    async def get_job(job_id: str) -> JobResponse:
        job = await job_store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        return JobResponse(
            job_id=job.id,
            status=job.status,
            topology=job.topology,
            output=job.output,
            error=job.error,
        )

    @app.get("/jobs/{job_id}/stream")
    async def stream_job(job_id: str) -> StreamingResponse:
        job = await job_store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

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

    signals: list[PipelineSignalDelivery] = []
    for pt in pipeline_targets:
        event = str(pt.get("emit"))
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
                event=event,
            )
        )

    return PipelineWebhookResponse(
        delivered=True, trigger=trigger_id, source=source, signals=signals
    )
