"""Async job execution, polling, SSE streaming, and webhook-trigger endpoints.

The run/webhook handlers are thin: they read app-state (runtime, canary, store, semaphore, config)
and delegate to :class:`JobService`, mapping its :class:`ServiceError` to a status code.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.persistence import Store

from ._config import ServerCfg
from ._helpers import (
    _check_webhook_signature,
    _get_runtime,
)
from ._jobs import JobStore
from ._schemas import (
    JobListItem,
    JobResponse,
    RunRequest,
)
from ._services import JobService, ServiceError

logger = logging.getLogger("swarmkit.server")


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
    async def webhook_trigger(topology_name: str, request: Request) -> JobResponse:
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
