"""SwarmKit HTTP server — persistent mode wrapping WorkspaceRuntime.

A FastAPI application that loads a workspace at startup and exposes
topology execution, validation, and introspection via HTTP endpoints.
The second interface over ``WorkspaceRuntime`` (the CLI is the first;
the v1.1 web UI will be the third).

See design §14.1 (persistent/scheduled mode).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from swael_runtime._workspace_runtime import (
    MissingMCPServerError,
    WorkspaceRuntime,
)
from swael_runtime.errors import ResolutionErrors


def create_app(workspace_path: Path) -> FastAPI:
    """Build the FastAPI app for a given workspace."""

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
        yield
        await runtime.close()

    app = FastAPI(
        title="SwarmKit",
        description="HTTP interface over a SwarmKit workspace.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ---- endpoints -------------------------------------------------------

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

    @app.post("/run/{topology_name}")
    async def run_topology(topology_name: str, body: RunRequest, request: Request) -> RunResponse:
        rt = _get_runtime(request)
        if topology_name not in rt.workspace.topologies:
            available = sorted(rt.workspace.topologies.keys())
            raise HTTPException(
                status_code=404,
                detail=f"Topology '{topology_name}' not found. Available: {available}",
            )
        try:
            result = await rt.run(
                topology_name,
                body.input,
                max_steps=body.max_steps,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return RunResponse(
            output=result.output,
            agent_results=result.agent_results,
        )

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

    return app


# ---- request / response models ------------------------------------------


class RunRequest(BaseModel):
    input: str
    max_steps: int = 10


class RunResponse(BaseModel):
    output: str
    agent_results: dict[str, str] = {}


# ---- helpers -------------------------------------------------------------


def _get_runtime(request: Request) -> WorkspaceRuntime:
    runtime: WorkspaceRuntime | None = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Workspace not loaded yet")
    return runtime
