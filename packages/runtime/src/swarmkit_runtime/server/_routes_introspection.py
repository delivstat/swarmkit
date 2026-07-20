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


def _span_to_dict(span: Any) -> dict[str, Any]:
    """Serialize a RecordedSpan tree (topology.run → agent.step → tool.call) for a UI waterfall."""
    return {
        "name": span.name,
        "start_ns": span.start_ns,
        "end_ns": span.end_ns,
        "duration_ms": round((span.end_ns - span.start_ns) / 1e6, 3),
        "attributes": dict(span.attributes),
        "error": span.error,
        "children": [_span_to_dict(child) for child in span.children],
    }


def _audit_event_to_dict(event: Any) -> dict[str, Any]:
    """Serialize an AuditEvent to JSON (UUID → str, datetime → isoformat). Read-only view."""
    ts = getattr(event, "timestamp", None)
    return {
        "event_id": str(getattr(event, "event_id", "")),
        "event_type": getattr(event, "event_type", ""),
        "agent_id": getattr(event, "agent_id", ""),
        "agent_role": getattr(event, "agent_role", None),
        "timestamp": ts.isoformat() if ts is not None else None,
        "topology_id": getattr(event, "topology_id", None),
        "skill_id": getattr(event, "skill_id", None),
        "run_id": getattr(event, "run_id", None),
        "payload": getattr(event, "payload", {}),
    }


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

    @app.get("/funnels")
    async def list_funnels(request: Request) -> list[str]:
        return sorted(_get_runtime(request).workspace.funnels.keys())

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

    @app.get("/api/schema/{artifact_type}")
    async def get_artifact_schema(artifact_type: str) -> dict[str, Any]:
        """The canonical JSON Schema for an artifact type — drives the UI's schema-generated
        designer (fields + enums + constraints, descriptions → tooltips). One of topology / skill /
        archetype / workspace / trigger; 404 otherwise."""
        from swarmkit_schema import SchemaName, get_schema  # noqa: PLC0415

        valid = set(SchemaName.__args__)  # type: ignore[attr-defined]
        if artifact_type not in valid:
            raise HTTPException(
                status_code=404,
                detail=f"unknown artifact type {artifact_type!r}; one of {sorted(valid)}",
            )
        return get_schema(artifact_type)  # type: ignore[arg-type]

    @app.get("/observability/runs/{run_id}/trace")
    async def get_run_trace(run_id: str, request: Request) -> dict[str, Any]:
        """The finished run's span tree (topology.run → agent.step → tool.call) for a UI waterfall,
        loaded from the persisted RunTrace (.swarmkit/traces/<run-id>.json)."""
        ws_path = getattr(request.app.state, "workspace_path", None)
        if ws_path is None:
            raise HTTPException(status_code=404, detail="workspace not ready")
        trace_file = ws_path / ".swarmkit" / "traces" / f"{run_id}.json"
        if not trace_file.is_file():
            raise HTTPException(status_code=404, detail=f"no trace recorded for run {run_id!r}")

        from swarmkit_runtime._workspace_runtime import _run_trace_to_span  # noqa: PLC0415
        from swarmkit_runtime.trace import RunTrace  # noqa: PLC0415

        runtime = getattr(request.app.state, "runtime", None)
        workspace_id = runtime.workspace_id if runtime is not None else ""
        return _span_to_dict(_run_trace_to_span(RunTrace.load(trace_file), workspace_id))

    @app.get("/audit")
    async def get_audit(
        request: Request,
        run_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Append-only audit events, newest-first (read-only; the media pillar exposes no
        update/delete). Empty when the workspace has no audit store yet."""
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is None:
            return []
        # Query the provider async, directly — the sync Observability.query_audit facade spins its
        # own event loop (for the CLI) and can't be called from inside this request's loop.
        events = [
            _audit_event_to_dict(e)
            async for e in runtime.audit_provider.query(
                run_id=run_id, agent_id=agent_id, limit=limit
            )
        ]
        return events

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

    @app.post("/canary/{topology_name}")
    async def canary_start(topology_name: str, request: Request) -> dict[str, Any]:
        """Start a canary at runtime (design 26 Layer B): split traffic to a newly-deployed version.
        Body: ``{base_version, canary_version, weight, promote_when?}``. Bootstraps the router when
        the instance had no canary configured. The canary version's artifact must already be
        deployed here (the fleet deploys it first)."""
        body = await request.json()
        base_version = body.get("base_version", "")
        canary_version = body.get("canary_version", "")
        weight = body.get("weight")
        if not (base_version and canary_version) or not isinstance(weight, int):
            raise HTTPException(
                status_code=400,
                detail="need 'base_version', 'canary_version', and integer 'weight'",
            )
        router: CanaryRouter | None = getattr(request.app.state, "canary_router", None)
        if router is None:
            router = CanaryRouter([])
            request.app.state.canary_router = router
        try:
            router.start_route(
                topology_name, base_version, canary_version, weight, body.get("promote_when")
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"started": True, "topology": topology_name, "routes": router.get_status()}
