"""Health, listing, validate, capabilities, usage, jobs-history and canary read/promote routes."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request

from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.persistence import SqliteStore

from ._helpers import (
    _build_capabilities,
    _get_runtime,
)


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
