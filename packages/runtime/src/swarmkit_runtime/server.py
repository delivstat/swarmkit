"""SwarmKit HTTP server — persistent mode wrapping WorkspaceRuntime.

A FastAPI application that loads a workspace at startup and exposes
topology execution, validation, and introspection via HTTP endpoints.
The second interface over ``WorkspaceRuntime`` (the CLI is the first;
the v1.1 web UI will be the third).

See design §14.1 (persistent/scheduled mode).
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from swarmkit_runtime._workspace_runtime import (
    MissingMCPServerError,
    WorkspaceRuntime,
)
from swarmkit_runtime.errors import ResolutionErrors

logger = logging.getLogger("swarmkit.server")

# ---- MCP optional import ---------------------------------------------------

_mcp_available = importlib.util.find_spec("mcp") is not None


# ---- Job model --------------------------------------------------------------


@dataclass
class Job:
    """In-memory representation of an async topology execution."""

    id: str
    topology: str
    status: Literal["pending", "running", "completed", "failed"]
    input: str
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


# ---- request / response models ----------------------------------------------


class RunRequest(BaseModel):
    input: str
    max_steps: int = 10


class CreateConversationRequest(BaseModel):
    topology: str


class SendMessageRequest(BaseModel):
    message: str


class RunResponse(BaseModel):
    output: str
    agent_results: dict[str, str] = {}


class JobResponse(BaseModel):
    job_id: str
    status: str
    output: str | None = None
    error: str | None = None


class JobListItem(BaseModel):
    job_id: str
    topology: str
    status: str
    created_at: str
    completed_at: str | None = None


# ---- job execution ----------------------------------------------------------


async def execute_job(job: Job, rt: WorkspaceRuntime, max_steps: int) -> None:
    """Run topology in background, updating job state."""
    job.status = "running"
    job.events.append(f"Job started for topology '{job.topology}'")
    try:
        result = await rt.run(
            job.topology,
            job.input,
            max_steps=max_steps,
        )
        job.output = result.output
        job.status = "completed"
        job.events.append("Job completed successfully")
    except Exception as exc:
        job.error = str(exc)
        job.status = "failed"
        job.events.append(f"Job failed: {exc}")
    finally:
        job.completed_at = datetime.now(UTC).isoformat()


def _start_job(job_store: JobStore, job: Job, rt: WorkspaceRuntime, max_steps: int) -> None:
    """Create a background task for a job and track it."""
    task = asyncio.create_task(execute_job(job, rt, max_steps))
    job_store.track_task(task)


# ---- endpoint registration --------------------------------------------------


def _register_introspection_routes(app: FastAPI) -> None:
    """Register health, topologies, skills, archetypes, validate endpoints."""

    @app.get("/health")
    async def health(request: Request) -> dict[str, str]:
        rt = _get_runtime(request)
        return {
            "status": "ok",
            "workspace": str(rt.workspace.raw.metadata.id),
        }

    @app.get("/topologies")
    async def list_topologies(request: Request) -> list[str]:
        return sorted(_get_runtime(request).workspace.topologies.keys())

    @app.get("/skills")
    async def list_skills(request: Request) -> list[dict[str, str]]:
        rt = _get_runtime(request)
        return [
            {"id": sid, "category": getattr(getattr(s.raw, "category", ""), "value", "")}
            for sid, s in sorted(rt.workspace.skills.items())
        ]

    @app.get("/archetypes")
    async def list_archetypes(request: Request) -> list[str]:
        return sorted(_get_runtime(request).workspace.archetypes.keys())

    @app.get("/validate")
    async def validate_workspace(request: Request) -> dict[str, Any]:
        rt = _get_runtime(request)
        ws = rt.workspace
        return {
            "valid": True,
            "workspace_id": str(ws.raw.metadata.id),
            "topologies": sorted(ws.topologies.keys()),
            "skills": sorted(ws.skills.keys()),
            "archetypes": sorted(ws.archetypes.keys()),
        }


def _register_job_routes(app: FastAPI, job_store: JobStore) -> None:
    """Register async job execution, polling, streaming, and webhook endpoints."""

    @app.post("/run/{topology_name}")
    async def run_topology(topology_name: str, body: RunRequest, request: Request) -> JobResponse:
        rt = _get_runtime(request)
        if topology_name not in rt.workspace.topologies:
            available = sorted(rt.workspace.topologies.keys())
            raise HTTPException(
                status_code=404,
                detail=f"Topology '{topology_name}' not found. Available: {available}",
            )
        job = await job_store.create(topology_name, body.input)
        _start_job(job_store, job, rt, body.max_steps)
        return JobResponse(job_id=job.id, status="running")

    @app.get("/jobs")
    async def list_jobs() -> list[JobListItem]:
        jobs = await job_store.list_all()
        return [
            JobListItem(
                job_id=j.id,
                topology=j.topology,
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
        if topology_name not in rt.workspace.topologies:
            available = sorted(rt.workspace.topologies.keys())
            raise HTTPException(
                status_code=404,
                detail=f"Topology '{topology_name}' not found. Available: {available}",
            )
        body = await request.json()
        user_input = body.get("input", str(body)) if isinstance(body, dict) else str(body)
        job = await job_store.create(topology_name, user_input)
        _start_job(job_store, job, rt, max_steps=10)
        return JobResponse(job_id=job.id, status="running")


def _register_conversation_routes(app: FastAPI, workspace_path: Path) -> None:
    """Register conversation CRUD endpoints."""

    @app.post("/conversations")
    async def create_conversation(
        body: CreateConversationRequest, request: Request
    ) -> dict[str, str]:
        from swarmkit_runtime._conversation import ConversationManager  # noqa: PLC0415

        rt = _get_runtime(request)
        manager = ConversationManager(rt, workspace_path)
        conv = manager.create(body.topology)
        return {"id": conv.id, "topology": conv.topology_name}

    @app.get("/conversations")
    async def list_conversations_endpoint(
        request: Request,
    ) -> list[dict[str, str]]:
        from swarmkit_runtime._conversation import ConversationManager  # noqa: PLC0415

        rt = _get_runtime(request)
        manager = ConversationManager(rt, workspace_path)
        return manager.list_conversations()

    @app.post("/conversations/{conversation_id}/messages")
    async def send_message(
        conversation_id: str, body: SendMessageRequest, request: Request
    ) -> dict[str, Any]:
        from swarmkit_runtime._conversation import ConversationManager  # noqa: PLC0415

        rt = _get_runtime(request)
        manager = ConversationManager(rt, workspace_path)
        conv = manager.resume(conversation_id)
        if conv is None:
            raise HTTPException(
                status_code=404,
                detail=f"Conversation '{conversation_id}' not found",
            )
        result = await manager.send(conv, body.message)
        return {
            "output": result.output,
            "turns": len(conv.turns),
            "conversation_id": conv.id,
        }


# ---- MCP endpoint (optional) ------------------------------------------------


def _mount_mcp(app: FastAPI) -> None:
    """Set up MCP server and mount on the FastAPI app.

    Called only when the ``mcp`` package is importable.
    """
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-untyped]  # noqa: PLC0415

    mcp_server = FastMCP("swarmkit")
    _tools_registered = False

    @app.middleware("http")
    async def _register_mcp_tools(request: Request, call_next):  # type: ignore[no-untyped-def]
        nonlocal _tools_registered
        if not _tools_registered and hasattr(request.app.state, "runtime"):
            rt: WorkspaceRuntime = request.app.state.runtime
            for name, topo in rt.workspace.topologies.items():
                description = topo.root.source_archetype or f"Run topology {name}"

                def _make_tool_fn(topo_name: str, desc: str, app_ref: FastAPI) -> None:
                    async def _run(input: str) -> str:
                        runtime: WorkspaceRuntime = app_ref.state.runtime
                        result = await runtime.run(topo_name, input)
                        return result.output

                    mcp_server.add_tool(
                        _run,
                        name=f"run_{topo_name}",
                        description=desc,
                    )

                _make_tool_fn(name, description, request.app)

                def _make_resource(topo_name: str, desc: str) -> None:
                    @mcp_server.resource(f"topology://{topo_name}")
                    async def _resource() -> str:
                        return f"Topology: {topo_name} -- {desc}"

                _make_resource(name, description)

            _tools_registered = True
            logger.info(
                "MCP tools registered for %d topologies",
                len(rt.workspace.topologies),
            )

        return await call_next(request)

    try:
        mcp_app = mcp_server.streamable_http_app()
        app.mount("/mcp", mcp_app)
        logger.info("MCP endpoint mounted at /mcp")
    except Exception:
        logger.warning("Failed to mount MCP endpoint", exc_info=True)


# ---- app factory ------------------------------------------------------------


def create_app(
    workspace_path: Path,
    *,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Build the FastAPI app for a given workspace."""

    job_store = JobStore()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        try:
            runtime = WorkspaceRuntime.from_workspace_path(workspace_path)
        except ResolutionErrors as exc:
            errors = [{"code": e.code, "message": e.message} for e in exc.errors]
            raise RuntimeError(f"Workspace failed to resolve: {errors}") from exc
        except MissingMCPServerError as exc:
            raise RuntimeError(str(exc)) from exc

        app.state.runtime = runtime

        try:
            await runtime.start_session()
            logger.info("MCP servers started at boot")
        except Exception:
            logger.warning(
                "MCP server boot failed; runs will manage per-invocation",
                exc_info=True,
            )

        yield
        await runtime.close()

    app = FastAPI(
        title="SwarmKit",
        description="HTTP interface over a SwarmKit workspace.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
        start = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start
        logger.info(
            "%s %s -> %s (%.3fs)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        return response

    # Routes
    _register_introspection_routes(app)
    _register_job_routes(app, job_store)
    _register_conversation_routes(app, workspace_path)

    if _mcp_available:
        _mount_mcp(app)
    else:
        logger.warning("mcp package not installed; /mcp endpoint disabled")

    return app


# ---- helpers ----------------------------------------------------------------


def _get_runtime(request: Request) -> WorkspaceRuntime:
    runtime: WorkspaceRuntime | None = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Workspace not loaded yet")
    return runtime
