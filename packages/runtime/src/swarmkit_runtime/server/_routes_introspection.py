"""Health, listing, validate, capabilities, usage, jobs-history and canary read/promote routes."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.persistence import Store

from ._helpers import (
    _build_capabilities,
    _build_instance_state,
    _filter_instance_state,
    _get_runtime,
    _instance_state_manifest,
)
from ._services import ArtifactService


class ArtifactRef(BaseModel):
    collection: str  # topologies | skills | archetypes | triggers
    id: str


class ArtifactFetchRequest(BaseModel):
    refs: list[ArtifactRef] = []


def _register_introspection_routes(app: FastAPI) -> None:  # noqa: PLR0915
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

    @app.get("/fleet/state")
    async def fleet_state(request: Request) -> dict[str, Any]:
        """Full observed state — every artifact's *content* (not just names like /capabilities).

        The fleet pulls this to populate its inventory and cache it (offline-resilient). Phase 1 of
        design/details/control-plane/19-fleet-enrollment-protocol.md. Requires `serve:read` — or,
        for an enrolled fleet, a valid membership key (monitor+), via the auth-seam fallback.
        """
        svc = ArtifactService(request.app.state.workspace_path)
        return _build_instance_state(_get_runtime(request), svc)

    @app.get("/fleet/state/manifest")
    async def fleet_state_manifest(request: Request) -> dict[str, Any]:
        """The names-only manifest of the observed state — every artifact's id/version/content_hash,
        *without* content. A fleet pulls this (cheap), diffs the hashes against its cache, and then
        fetches only the changed bodies via POST /fleet/state/artifacts — the delta-sync primitive
        (design 19 §delta sync). Same auth as /fleet/state (serve:read or a monitor+ membership)."""
        svc = ArtifactService(request.app.state.workspace_path)
        return _instance_state_manifest(_build_instance_state(_get_runtime(request), svc))

    @app.post("/fleet/state/artifacts")
    async def fleet_state_artifacts(req: ArtifactFetchRequest, request: Request) -> dict[str, Any]:
        """Fetch the *content* of specific artifacts (the body-fetch half of delta sync). The body
        lists ``refs: [{collection, id}]`` (collection = topologies/skills/archetypes/triggers);
        the response is an InstanceState carrying only those artifacts. A read despite the POST verb
        (POST only to carry the ref list). Same auth as /fleet/state."""
        svc = ArtifactService(request.app.state.workspace_path)
        state = _build_instance_state(_get_runtime(request), svc)
        refs = [(r.collection, r.id) for r in req.refs]
        return _filter_instance_state(state, refs)

    @app.get("/triggers")
    async def list_triggers(request: Request) -> list[dict[str, Any]]:
        trigger_configs: list[dict[str, Any]] = getattr(request.app.state, "trigger_configs", [])
        return trigger_configs

    @app.get("/usage")
    async def get_usage(request: Request) -> dict[str, Any]:
        s: Store | None = getattr(request.app.state, "store", None)
        if s is None:
            return {"summary": {}, "by_model": []}
        return {
            "summary": s.get_usage_summary(),
            "by_model": s.get_usage_by_model(),
        }

    @app.get("/usage/{job_id}")
    async def get_job_usage(job_id: str, request: Request) -> dict[str, Any]:
        s: Store | None = getattr(request.app.state, "store", None)
        if s is None:
            return {}
        return s.get_usage_summary(job_id=job_id)

    @app.get("/jobs/history")
    async def list_persisted_jobs(request: Request) -> list[dict[str, Any]]:
        s: Store | None = getattr(request.app.state, "store", None)
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
