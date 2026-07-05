"""Async job execution, polling, SSE streaming, and webhook-trigger endpoints."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.persistence import SqliteStore

from ._config import ServerCfg
from ._helpers import (
    _check_webhook_signature,
    _get_runtime,
)
from ._jobs import JobStore, _start_job
from ._schemas import (
    JobListItem,
    JobResponse,
    RunRequest,
)

logger = logging.getLogger("swarmkit.server")


def _register_job_routes(app: FastAPI, job_store: JobStore) -> None:  # noqa: PLR0915
    """Register async job execution, polling, streaming, and webhook endpoints."""

    @app.post("/run/{topology_name}")
    async def run_topology(topology_name: str, body: RunRequest, request: Request) -> JobResponse:
        rt = _get_runtime(request)
        canary: CanaryRouter | None = getattr(request.app.state, "canary_router", None)

        resolved_name = topology_name
        selected_version: str | None = None
        if canary and canary.has_route(topology_name):
            selected_version = canary.select(topology_name)
            resolved_name = f"{topology_name}@{selected_version}"

        if resolved_name not in rt.workspace.topologies:
            if topology_name not in rt.workspace.topologies:
                available = sorted(rt.workspace.topologies.keys())
                raise HTTPException(
                    status_code=404,
                    detail=f"Topology '{topology_name}' not found. Available: {available}",
                )
            resolved_name = topology_name
            selected_version = None

        semaphore: asyncio.Semaphore | None = getattr(request.app.state, "job_semaphore", None)
        if semaphore is not None and semaphore.locked():
            raise HTTPException(
                status_code=429,
                detail="Max concurrent jobs reached. Try again later.",
            )
        cfg: ServerCfg = getattr(request.app.state, "server_config", ServerCfg())
        sqlite_store: SqliteStore | None = getattr(request.app.state, "store", None)
        job = await job_store.create(resolved_name, body.input)
        job.version = selected_version
        if sqlite_store:
            sqlite_store.create_job(job.id, resolved_name, body.input)
            if selected_version:
                sqlite_store.update_job(job.id, version=selected_version)
        _start_job(
            job_store,
            job,
            rt,
            body.max_steps,
            timeout_seconds=cfg.timeout_seconds,
            semaphore=semaphore,
            canary_router=canary,
            store=sqlite_store,
        )
        return JobResponse(
            job_id=job.id,
            status="running",
            output=None,
            error=None,
        )

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
        canary: CanaryRouter | None = getattr(request.app.state, "canary_router", None)

        resolved_name = topology_name
        selected_version: str | None = None
        if canary and canary.has_route(topology_name):
            selected_version = canary.select(topology_name)
            resolved_name = f"{topology_name}@{selected_version}"

        if resolved_name not in rt.workspace.topologies:
            if topology_name not in rt.workspace.topologies:
                available = sorted(rt.workspace.topologies.keys())
                raise HTTPException(
                    status_code=404,
                    detail=f"Topology '{topology_name}' not found. Available: {available}",
                )
            resolved_name = topology_name
            selected_version = None

        semaphore: asyncio.Semaphore | None = getattr(request.app.state, "job_semaphore", None)
        if semaphore is not None and semaphore.locked():
            raise HTTPException(
                status_code=429,
                detail="Max concurrent jobs reached. Try again later.",
            )
        cfg: ServerCfg = getattr(request.app.state, "server_config", ServerCfg())

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
        sqlite_store: SqliteStore | None = getattr(request.app.state, "store", None)
        job = await job_store.create(resolved_name, user_input)
        job.version = selected_version
        if sqlite_store:
            sqlite_store.create_job(job.id, resolved_name, user_input)
            if selected_version:
                sqlite_store.update_job(job.id, version=selected_version)
        _start_job(
            job_store,
            job,
            rt,
            max_steps=10,
            timeout_seconds=cfg.timeout_seconds,
            semaphore=semaphore,
            canary_router=canary,
            store=sqlite_store,
        )
        return JobResponse(job_id=job.id, status="running")
