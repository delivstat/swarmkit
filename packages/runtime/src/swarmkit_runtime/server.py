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
import json
import logging
import os
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
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from swarmkit_runtime._workspace_runtime import (
    MissingMCPServerError,
    WorkspaceRuntime,
)
from swarmkit_runtime.auth import AuthError, AuthProvider, NoneAuthProvider
from swarmkit_runtime.auth import AuthRequest as AuthReq
from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.persistence import SqliteStore, create_store
from swarmkit_runtime.triggers import TriggerScheduler
from swarmkit_runtime.triggers._webhook import validate_webhook_signature

logger = logging.getLogger("swarmkit.server")

# ---- server config -----------------------------------------------------------

_DEFAULT_MAX_CONCURRENT = 5
_DEFAULT_TIMEOUT_SECONDS = 300
_DEFAULT_MCP_ENABLED = True


@dataclass(frozen=True)
class ServerCfg:
    """Parsed ``server:`` block from workspace.yaml."""

    max_concurrent: int = _DEFAULT_MAX_CONCURRENT
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS
    mcp_enabled: bool = _DEFAULT_MCP_ENABLED


def _parse_server_config(workspace: Any) -> ServerCfg:
    """Extract server config from the resolved workspace's raw model."""
    server_raw = getattr(workspace.raw, "server", None)
    if server_raw is None:
        return ServerCfg()
    jobs = getattr(server_raw, "jobs", None)
    mcp = getattr(server_raw, "mcp", None)
    return ServerCfg(
        max_concurrent=(getattr(jobs, "max_concurrent", None) or _DEFAULT_MAX_CONCURRENT),
        timeout_seconds=(getattr(jobs, "timeout_seconds", None) or _DEFAULT_TIMEOUT_SECONDS),
        mcp_enabled=(
            bool(getattr(mcp, "enabled", _DEFAULT_MCP_ENABLED))
            if mcp is not None
            else _DEFAULT_MCP_ENABLED
        ),
    )


def _parse_canary_routes(workspace: Any) -> list[dict[str, Any]]:
    """Extract canary route configs from workspace.yaml server.canary block."""
    server_raw = getattr(workspace.raw, "server", None)
    if server_raw is None:
        return []
    canary = getattr(server_raw, "canary", None)
    if canary is None:
        return []
    routes = getattr(canary, "routes", None) or []
    result: list[dict[str, Any]] = []
    for route in routes:
        versions = []
        for v in route.versions:
            entry: dict[str, Any] = {"version": v.version, "weight": v.weight}
            if v.promote_when is not None:
                entry["promote_when"] = v.promote_when.model_dump(exclude_none=True)
            versions.append(entry)
        result.append({"topology": route.topology, "versions": versions})
    return result


def _parse_trigger_configs(workspace: Any) -> list[dict[str, Any]]:
    """Convert resolved triggers to plain dicts for the scheduler."""
    configs: list[dict[str, Any]] = []
    triggers = getattr(workspace, "triggers", ()) or ()
    for rt in triggers:
        raw = rt.raw
        config_obj = getattr(raw, "config", None)
        config_dict: dict[str, Any] = {}
        if config_obj is not None:
            config_dict = dict(config_obj.model_dump(exclude_none=True))
        configs.append(
            {
                "id": rt.id,
                "type": raw.type.value,
                "enabled": raw.enabled if raw.enabled is not None else True,
                "targets": list(rt.targets),
                "config": config_dict,
            }
        )
    return configs


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
    version: str | None = None
    status: str
    created_at: str
    completed_at: str | None = None


# ---- job execution ----------------------------------------------------------


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


# ---- webhook signature validation -------------------------------------------


def _check_webhook_signature(
    request: Request,
    raw_body: bytes,
    topology_name: str,
) -> None:
    """Validate HMAC signature for webhook triggers that have auth configured.

    Raises HTTPException(401) when a matching trigger config requires auth and
    the signature is absent or incorrect.  No-ops when no trigger requires auth.
    """
    trigger_configs: list[dict[str, Any]] = getattr(request.app.state, "trigger_configs", [])
    for tc in trigger_configs:
        if tc.get("type") != "webhook":
            continue
        if topology_name not in tc.get("targets", []):
            continue
        config = tc.get("config") or {}
        auth = config.get("auth") or {}
        secret_ref = auth.get("credentials_ref") or config.get("secret_ref")
        if not secret_ref:
            continue
        secret = os.environ.get(secret_ref, "")
        if not secret:
            logger.warning(
                "Webhook trigger secret_ref=%r not found in environment; "
                "skipping signature validation for topology=%r",
                secret_ref,
                topology_name,
            )
            continue
        header_name = auth.get("header", "X-Hub-Signature-256")
        sig = request.headers.get(header_name, "")
        if not validate_webhook_signature(raw_body, sig, secret):
            logger.warning(
                "Webhook signature validation failed for topology=%r",
                topology_name,
            )
            raise HTTPException(
                status_code=401,
                detail="Invalid webhook signature",
            )
        break


# ---- endpoint registration --------------------------------------------------


def _register_introspection_routes(app: FastAPI) -> None:
    """Register health, topologies, skills, archetypes, validate, triggers endpoints."""

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

    @app.get("/capabilities")
    async def capabilities(request: Request) -> dict[str, Any]:
        """What this instance can do — the control plane reads this at enroll/refresh."""
        return _build_capabilities(_get_runtime(request))

    @app.get("/triggers")
    async def list_triggers(request: Request) -> list[dict[str, Any]]:
        trigger_configs: list[dict[str, Any]] = getattr(request.app.state, "trigger_configs", [])
        return trigger_configs

    @app.get("/usage")
    async def get_usage(request: Request) -> dict[str, Any]:
        s: SqliteStore | None = getattr(request.app.state, "store", None)
        if s is None:
            return {"summary": {}, "by_model": []}
        return {
            "summary": s.get_usage_summary(),
            "by_model": s.get_usage_by_model(),
        }

    @app.get("/usage/{job_id}")
    async def get_job_usage(job_id: str, request: Request) -> dict[str, Any]:
        s: SqliteStore | None = getattr(request.app.state, "store", None)
        if s is None:
            return {}
        return s.get_usage_summary(job_id=job_id)

    @app.get("/jobs/history")
    async def list_persisted_jobs(request: Request) -> list[dict[str, Any]]:
        s: SqliteStore | None = getattr(request.app.state, "store", None)
        if s is None:
            return []
        rows = s.list_jobs(limit=100)
        return [
            {
                "job_id": r.id,
                "topology": r.topology,
                "version": r.version,
                "status": r.status,
                "created_at": r.created_at,
                "completed_at": r.completed_at,
                "usage_input_tokens": r.usage_input_tokens,
                "usage_output_tokens": r.usage_output_tokens,
                "usage_cost_usd": r.usage_cost_usd,
            }
            for r in rows
        ]

    @app.get("/canary")
    async def canary_status(request: Request) -> dict[str, Any]:
        router: CanaryRouter | None = getattr(request.app.state, "canary_router", None)
        if router is None:
            return {"enabled": False, "routes": []}
        return {
            "enabled": True,
            "routes": router.get_status(),
            "promotions": router.get_promotions(),
        }

    @app.post("/canary/{topology_name}/promote")
    async def canary_promote(topology_name: str, request: Request) -> dict[str, Any]:
        router: CanaryRouter | None = getattr(request.app.state, "canary_router", None)
        if router is None:
            raise HTTPException(status_code=404, detail="Canary routing not configured")
        body = await request.json()
        version = body.get("version", "")
        if not version:
            raise HTTPException(status_code=400, detail="Missing 'version' in request body")
        if not router.promote(topology_name, version):
            raise HTTPException(
                status_code=404,
                detail=f"No canary route for topology '{topology_name}' version '{version}'",
            )
        return {"promoted": True, "topology": topology_name, "version": version}

    @app.post("/canary/{topology_name}/rollback")
    async def canary_rollback(topology_name: str, request: Request) -> dict[str, Any]:
        router: CanaryRouter | None = getattr(request.app.state, "canary_router", None)
        if router is None:
            raise HTTPException(status_code=404, detail="Canary routing not configured")
        if not router.rollback(topology_name):
            raise HTTPException(
                status_code=404,
                detail=f"No canary route for topology '{topology_name}'",
            )
        return {"rolled_back": True, "topology": topology_name}


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


def _register_conversation_routes(app: FastAPI, workspace_path: Path) -> None:  # noqa: PLR0915
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

    @app.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
        from swarmkit_runtime._conversation import ConversationManager  # noqa: PLC0415

        rt = _get_runtime(request)
        manager = ConversationManager(rt, workspace_path)
        conv = manager.resume(conversation_id)
        if conv is None:
            raise HTTPException(
                status_code=404,
                detail=f"Conversation '{conversation_id}' not found",
            )
        return {
            "id": conv.id,
            "topology": conv.topology_name,
            "turns": [
                {
                    "role": t.role,
                    "content": t.content,
                    "timestamp": t.timestamp,
                }
                for t in conv.turns
            ],
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
        }

    @app.post("/conversations/{conversation_id}/messages")
    async def send_message(
        conversation_id: str, body: SendMessageRequest, request: Request
    ) -> StreamingResponse:
        from swarmkit_runtime._conversation import ConversationManager  # noqa: PLC0415

        rt = _get_runtime(request)
        manager = ConversationManager(rt, workspace_path)
        conv = manager.resume(conversation_id)
        if conv is None:
            raise HTTPException(
                status_code=404,
                detail=f"Conversation '{conversation_id}' not found",
            )

        async def stream_response() -> AsyncGenerator[str]:
            from swarmkit_runtime.langgraph_compiler._helpers import (  # noqa: PLC0415
                _progress_listeners,
            )

            progress_lines: list[str] = []

            def on_progress(msg: str) -> None:
                text = msg.strip()
                if text:
                    progress_lines.append(text)

            _progress_listeners.append(on_progress)
            send_task = asyncio.create_task(manager.send(conv, body.message))

            sent = 0
            try:
                while not send_task.done():
                    await asyncio.sleep(0.3)
                    new_lines = progress_lines[sent:]
                    for line in new_lines:
                        yield f"data: {json.dumps({'type': 'progress', 'text': line})}\n\n"
                        sent += 1
            finally:
                if on_progress in _progress_listeners:
                    _progress_listeners.remove(on_progress)

            for line in progress_lines[sent:]:
                yield f"data: {json.dumps({'type': 'progress', 'text': line})}\n\n"

            try:
                result = send_task.result()
                events = [
                    {
                        "event_type": e.event_type,
                        "agent_id": e.agent_id,
                        "timestamp": e.timestamp,
                        "duration_ms": e.payload.get("duration_ms"),
                        "model": e.payload.get("model"),
                        "tokens": e.payload.get("usage_tokens"),
                    }
                    for e in result.events
                ]
                usage_data = None
                if result.usage:
                    usage_data = {
                        "input_tokens": result.usage.input_tokens,
                        "output_tokens": result.usage.output_tokens,
                        "total_tokens": result.usage.total_tokens,
                        "by_model": result.usage.by_model,
                    }
                done_payload: dict[str, object] = {
                    "type": "done",
                    "output": result.output,
                    "turns": len(conv.turns),
                    "conversation_id": conv.id,
                    "events": events,
                    "usage": usage_data,
                }
                if result.trace_data:
                    done_payload["trace"] = result.trace_data
                yield f"data: {json.dumps(done_payload)}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

        return StreamingResponse(
            stream_response(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )


# ---- MCP endpoint (optional) ------------------------------------------------


def _mount_mcp(app: FastAPI) -> None:
    """Set up MCP server and mount on the FastAPI app.

    Called only when the ``mcp`` package is importable.
    """
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415

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


# ---- lifespan helpers -------------------------------------------------------


async def _boot_mcp(runtime: WorkspaceRuntime, cfg: ServerCfg) -> None:
    """Start MCP servers at boot when enabled."""
    if cfg.mcp_enabled:
        try:
            await runtime.start_session()
            logger.info("MCP servers started at boot")
        except Exception:
            logger.warning(
                "MCP server boot failed; runs will manage per-invocation",
                exc_info=True,
            )
    else:
        logger.info("MCP server boot disabled by server.mcp.enabled=false")


# ---- scheduler factory ------------------------------------------------------


async def _start_scheduler(
    app: FastAPI,
    job_store: JobStore,
    trigger_configs: list[dict[str, Any]],
) -> TriggerScheduler:
    """Create and start a TriggerScheduler wired to the app's job store."""

    async def _fire_trigger(topology_name: str, source: str) -> None:
        rt: WorkspaceRuntime = app.state.runtime
        sema: asyncio.Semaphore | None = getattr(app.state, "job_semaphore", None)
        server_cfg: ServerCfg = getattr(app.state, "server_config", ServerCfg())
        job = await job_store.create(topology_name, source)
        _start_job(
            job_store,
            job,
            rt,
            max_steps=10,
            timeout_seconds=server_cfg.timeout_seconds,
            semaphore=sema,
        )
        logger.info(
            "Trigger fired topology=%r job_id=%s source=%r",
            topology_name,
            job.id,
            source,
        )

    scheduler = TriggerScheduler(trigger_configs, _fire_trigger)
    await scheduler.start()
    return scheduler


# ---- CRUD routes for topology/skill/archetype editing -----------------------


def _register_crud_routes(app: FastAPI, workspace_path: Path) -> None:  # noqa: PLR0915
    """Register CRUD endpoints for topology, skill, and archetype YAML files."""
    import yaml as _yaml  # noqa: PLC0415

    from swarmkit_runtime.resolver import resolve_workspace  # noqa: PLC0415

    def _artifact_dir(kind: str) -> Path:
        mapping = {
            "topology": "topologies",
            "skill": "skills",
            "archetype": "archetypes",
        }
        return workspace_path / mapping[kind]

    def _find_file(kind: str, artifact_id: str) -> Path | None:
        d = _artifact_dir(kind)
        if not d.is_dir():
            return None
        for f in d.rglob("*.yaml"):
            try:
                raw = _yaml.safe_load(f.read_text()) or {}
                meta = raw.get("metadata", {})
                file_id = meta.get("id", "")
                file_name = meta.get("name", "")
                if artifact_id in (file_id, file_name):
                    return f
            except Exception:
                continue
        return None

    def _validate_workspace() -> dict[str, Any]:
        try:
            ws = resolve_workspace(workspace_path)
            return {
                "valid": True,
                "topologies": sorted(ws.topologies.keys()),
                "skills": sorted(ws.skills.keys()),
                "archetypes": sorted(ws.archetypes.keys()),
            }
        except ResolutionErrors as exc:
            return {
                "valid": False,
                "errors": [{"code": e.code, "message": e.message} for e in exc.errors],
            }

    def _reload_runtime(request: Request) -> None:
        try:
            new_rt = WorkspaceRuntime.from_workspace_path(workspace_path)
            request.app.state.runtime = new_rt
        except Exception:
            logger.warning("Runtime reload failed after CRUD write", exc_info=True)

    # ---- Read detail endpoints ----

    @app.get("/api/topologies/{topology_id}")
    async def get_topology_detail(topology_id: str, request: Request) -> dict[str, Any]:
        rt = _get_runtime(request)
        topo = rt.workspace.topologies.get(topology_id)
        if topo is None:
            raise HTTPException(status_code=404, detail=f"Topology '{topology_id}' not found")

        def _agent_to_dict(agent: Any) -> dict[str, Any]:
            result: dict[str, Any] = {
                "id": agent.id,
                "role": agent.role,
                "source_archetype": agent.source_archetype,
                "model": dict(agent.model) if agent.model else None,
                "skills": [s.id for s in agent.skills],
            }
            if agent.children:
                result["children"] = [_agent_to_dict(c) for c in agent.children]
            return result

        return {
            "id": topo.id,
            "version": topo.raw.metadata.version,
            "description": getattr(topo.raw.metadata, "description", None),
            "resolved": _agent_to_dict(topo.root),
        }

    @app.get("/api/topologies/{topology_id}/yaml")
    async def get_topology_yaml(topology_id: str) -> dict[str, str]:
        f = _find_file("topology", topology_id)
        if f is None:
            raise HTTPException(status_code=404, detail=f"Topology '{topology_id}' not found")
        return {"yaml": f.read_text()}

    @app.get("/api/skills/{skill_id}/yaml")
    async def get_skill_yaml(skill_id: str) -> dict[str, str]:
        f = _find_file("skill", skill_id)
        if f is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
        return {"yaml": f.read_text()}

    @app.get("/api/archetypes/{archetype_id}/yaml")
    async def get_archetype_yaml(archetype_id: str) -> dict[str, str]:
        f = _find_file("archetype", archetype_id)
        if f is None:
            raise HTTPException(status_code=404, detail=f"Archetype '{archetype_id}' not found")
        return {"yaml": f.read_text()}

    @app.get("/api/archetypes/{archetype_id}")
    async def get_archetype_detail(archetype_id: str, request: Request) -> dict[str, Any]:
        rt = _get_runtime(request)
        arch = rt.workspace.archetypes.get(archetype_id)
        if arch is None:
            raise HTTPException(status_code=404, detail=f"Archetype '{archetype_id}' not found")
        raw = arch.raw
        defaults = getattr(raw, "defaults", None)
        return {
            "id": archetype_id,
            "name": raw.metadata.name,
            "description": getattr(raw.metadata, "description", ""),
            "role": raw.role,
            "defaults": {
                "model": (
                    dict(defaults.model) if defaults and getattr(defaults, "model", None) else None
                ),
                "skills": [
                    s if isinstance(s, str) else getattr(s, "capability", str(s))
                    for s in (getattr(defaults, "skills", None) or [])
                ]
                if defaults
                else [],
            },
        }

    @app.get("/api/skills/{skill_id}")
    async def get_skill_detail(skill_id: str, request: Request) -> dict[str, Any]:
        rt = _get_runtime(request)
        skill = rt.workspace.skills.get(skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
        raw = skill.raw
        impl = getattr(raw, "implementation", None)
        return {
            "id": skill_id,
            "name": raw.metadata.name,
            "description": getattr(raw.metadata, "description", ""),
            "category": getattr(raw.category, "value", str(raw.category)),
            "implementation_type": getattr(impl, "type", None) if impl else None,
        }

    # ---- Write endpoints ----

    @app.put("/api/topologies/{topology_id}")
    async def put_topology(topology_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        yaml_content = body.get("yaml", "")
        dry_run = body.get("dry_run", False)

        try:
            _yaml.safe_load(yaml_content)
        except Exception as exc:
            return {
                "valid": False,
                "errors": [{"code": "yaml.parse", "message": str(exc)}],
            }

        if not dry_run:
            f = _find_file("topology", topology_id)
            if f is None:
                f = _artifact_dir("topology") / f"{topology_id}.yaml"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(yaml_content)

        result = _validate_workspace()
        if result["valid"] and not dry_run:
            _reload_runtime(request)
        elif not result["valid"] and not dry_run and f:
            pass

        return result

    @app.post("/api/topologies")
    async def create_topology(request: Request) -> dict[str, Any]:
        body = await request.json()
        yaml_content = body.get("yaml", "")

        try:
            parsed = _yaml.safe_load(yaml_content)
            name = parsed.get("metadata", {}).get("name", "")
        except Exception as exc:
            return {
                "valid": False,
                "errors": [{"code": "yaml.parse", "message": str(exc)}],
            }

        if not name:
            return {
                "valid": False,
                "errors": [{"code": "missing.name", "message": "metadata.name required"}],
            }

        f = _artifact_dir("topology") / f"{name}.yaml"
        if f.exists():
            return {
                "valid": False,
                "errors": [{"code": "exists", "message": f"'{name}' already exists"}],
            }

        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(yaml_content)

        result = _validate_workspace()
        if result["valid"]:
            _reload_runtime(request)
        return result

    @app.delete("/api/topologies/{topology_id}")
    async def delete_topology(topology_id: str, request: Request) -> dict[str, Any]:
        f = _find_file("topology", topology_id)
        if f is None:
            raise HTTPException(status_code=404, detail=f"Topology '{topology_id}' not found")
        f.unlink()
        _reload_runtime(request)
        return {"deleted": True, "id": topology_id}

    @app.put("/api/skills/{skill_id}")
    async def put_skill(skill_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        yaml_content = body.get("yaml", "")
        dry_run = body.get("dry_run", False)

        f = _find_file("skill", skill_id)
        if f is None:
            f = _artifact_dir("skill") / f"{skill_id}.yaml"

        if not dry_run:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(yaml_content)

        result = _validate_workspace()
        if result["valid"] and not dry_run:
            _reload_runtime(request)
        return result

    @app.put("/api/archetypes/{archetype_id}")
    async def put_archetype(archetype_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        yaml_content = body.get("yaml", "")
        dry_run = body.get("dry_run", False)

        f = _find_file("archetype", archetype_id)
        if f is None:
            f = _artifact_dir("archetype") / f"{archetype_id}.yaml"

        if not dry_run:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(yaml_content)

        result = _validate_workspace()
        if result["valid"] and not dry_run:
            _reload_runtime(request)
        return result

    @app.post("/api/reload")
    async def reload_workspace(request: Request) -> dict[str, Any]:
        _reload_runtime(request)
        return _validate_workspace()


# ---- route authorization ----------------------------------------------------


def _required_action(method: str, path: str) -> str | None:
    """The serve:* tier action a route requires, or None for auth-exempt.

    read = all GETs (observe); admin = artifact mutation (/api/* writes) + canary
    promote/rollback; run = every other write (run/hooks/conversations/mcp). See
    design/details/control-plane/12-auth.md §4.
    """
    if path == "/health":
        return None
    if method.upper() == "GET":
        return "read"
    if path.startswith("/api/"):
        return "admin"
    if path.startswith("/canary/") and (path.endswith("/promote") or path.endswith("/rollback")):
        return "admin"
    return "run"


def _pkg_version(name: str) -> str:
    """Installed package version, or 'unknown'."""
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def _enum_value(obj: Any, attr: str, default: str = "") -> str:
    """Read a (possibly Enum-valued) attribute as its string, or default."""
    val = getattr(obj, attr, None)
    val = getattr(val, "value", val)  # pydantic Enum -> str
    return val if isinstance(val, str) else default


def _build_capabilities(rt: Any) -> dict[str, Any]:
    """Advertise what this instance can do — consumed by the control plane at enroll/refresh.

    See design/details/control-plane/13-connector-registry.md.
    """
    ws = rt.workspace
    raw = ws.raw
    server_raw = getattr(raw, "server", None)
    canary = getattr(server_raw, "canary", None)
    return {
        "serve_version": _pkg_version("swarmkit-runtime"),
        "schema_version": _pkg_version("swarmkit-schema"),
        "workspace_id": str(raw.metadata.id),
        "topologies": sorted(ws.topologies.keys()),
        "model_providers": rt.provider_registry.provider_ids,
        "governance_provider": _enum_value(getattr(raw, "governance", None), "provider", "mock"),
        "features": {
            "auth": _enum_value(getattr(server_raw, "auth", None), "provider", "none"),
            "compression": _enum_value(getattr(raw, "context_compression", None), "backend", "off"),
            "canary": bool(getattr(canary, "routes", None)),
        },
    }


def _record_serve_access(request: Any, identity: Any, action: str | None, status: int) -> None:
    """Append a serve access-audit record (best-effort; never breaks the request)."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        return
    try:
        store.record_access(
            client_id=identity.client_id,
            provider=identity.provider,
            method=request.method,
            path=request.url.path,
            action=action,
            status=status,
        )
    except Exception:  # audit must not break serving
        logger.debug("serve access-audit write failed", exc_info=True)


# ---- app factory ------------------------------------------------------------


def create_app(  # noqa: PLR0915
    workspace_path: Path,
    *,
    cors_origins: list[str] | None = None,
    auth_provider: AuthProvider | None = None,
) -> FastAPI:
    """Build the FastAPI app for a given workspace."""

    _auth = auth_provider or NoneAuthProvider()
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
        app.state.store = create_store(workspace_path, runtime.workspace.raw)

        # Parse server config from workspace.yaml
        cfg = _parse_server_config(runtime.workspace)
        app.state.server_config = cfg
        app.state.job_semaphore = asyncio.Semaphore(cfg.max_concurrent)
        logger.info(
            "Server config: max_concurrent=%d, timeout=%ds, mcp_enabled=%s",
            cfg.max_concurrent,
            cfg.timeout_seconds,
            cfg.mcp_enabled,
        )

        await _boot_mcp(runtime, cfg)

        # Build trigger configs and start the cron scheduler
        trigger_configs = _parse_trigger_configs(runtime.workspace)
        app.state.trigger_configs = trigger_configs
        scheduler = await _start_scheduler(app, job_store, trigger_configs)
        app.state.scheduler = scheduler

        # Initialize canary router if configured
        canary_routes = _parse_canary_routes(runtime.workspace)
        if canary_routes:
            available: dict[str, set[str]] = {
                name.split("@")[0]: set() for name in runtime.workspace.topologies
            }
            for name in runtime.workspace.topologies:
                base = name.split("@")[0]
                topo = runtime.workspace.topologies[name]
                available[base].add(topo.raw.metadata.version)
            app.state.canary_router = CanaryRouter(canary_routes, available)
        else:
            app.state.canary_router = None

        yield
        await scheduler.stop()
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

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Skip auth for health endpoint
        if request.url.path == "/health":
            return await call_next(request)

        auth_req = AuthReq(
            headers=dict(request.headers),
            path=request.url.path,
            method=request.method,
            query_params=dict(request.query_params),
            client_ip=request.client.host if request.client else None,
        )
        try:
            identity = await _auth.authenticate(auth_req)
            request.state.identity = identity
            logger.debug(
                "auth.success client_id=%s provider=%s",
                identity.client_id,
                identity.provider,
            )
        except AuthError as exc:
            logger.warning(
                "auth.denied path=%s reason=%s",
                request.url.path,
                str(exc),
            )
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": str(exc)},
            )

        # Per-route authorization: each route requires a serve:* tier scope.
        required = _required_action(request.method, request.url.path)
        if required is not None and not await _auth.authorize(identity, "serve", required):
            logger.warning(
                "authz.denied client_id=%s path=%s required=serve:%s",
                identity.client_id,
                request.url.path,
                required,
            )
            _record_serve_access(request, identity, required, 403)
            return JSONResponse(
                status_code=403,
                content={"error": f"Insufficient scope: requires serve:{required}"},
            )

        response = await call_next(request)
        # Audit mutating calls (run/admin) with the acting client_id.
        if required in ("run", "admin"):
            _record_serve_access(request, identity, required, response.status_code)
        return response

    # Routes
    _register_introspection_routes(app)
    _register_job_routes(app, job_store)
    _register_conversation_routes(app, workspace_path)
    _register_crud_routes(app, workspace_path)

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
