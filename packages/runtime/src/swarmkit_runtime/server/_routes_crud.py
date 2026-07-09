"""CRUD endpoints for topology / skill / archetype YAML editing (authoring surface).

Thin: each handler reads the request, calls :class:`ArtifactService`, maps its
:class:`ServiceError` to a status, and (for a mutation) installs the rebuilt runtime the service
returns onto ``app.state.runtime``.
"""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime

from ._helpers import _get_runtime, _verify_signed_deploy
from ._services import ArtifactService, ServiceError


def _install(request: Request, new_rt: WorkspaceRuntime | None) -> None:
    """Swap in a rebuilt runtime after a successful CRUD write (no-op when the reload failed)."""
    if new_rt is not None:
        request.app.state.runtime = new_rt


async def _put(
    request: Request, service: ArtifactService, kind: str, artifact_id: str, *, parse_check: bool
) -> dict[str, Any]:
    """Apply a PUT to an artifact. Accepts ``{yaml}`` (operator edit) or ``{content}`` (a fleet
    deploy — a dict; design 22). For a ``content`` deploy the fleet signature is verified against
    the pinned key before applying; then the dict is serialised for the workspace writer."""
    body = await request.json()
    content = body.get("content")
    if content is not None:
        _verify_signed_deploy(
            request, kind, artifact_id, content, body.get("fleet_id"), body.get("deploy_seq")
        )
        yaml_text = yaml.safe_dump(content, sort_keys=False)
    else:
        yaml_text = body.get("yaml", "")
    result, new_rt = service.put_yaml(
        kind, artifact_id, yaml_text, dry_run=body.get("dry_run", False), parse_check=parse_check
    )
    _install(request, new_rt)
    return result


def _register_crud_routes(app: FastAPI, service: ArtifactService) -> None:
    """Register CRUD endpoints for topology, skill, and archetype YAML files."""

    # ---- Read detail endpoints ----

    @app.get("/api/topologies/{topology_id}")
    async def get_topology_detail(topology_id: str, request: Request) -> dict[str, Any]:
        try:
            return service.topology_detail(_get_runtime(request), topology_id)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @app.get("/api/topologies/{topology_id}/yaml")
    async def get_topology_yaml(topology_id: str) -> dict[str, str]:
        try:
            return {"yaml": service.read_yaml("topology", topology_id)}
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @app.get("/api/skills/{skill_id}/yaml")
    async def get_skill_yaml(skill_id: str) -> dict[str, str]:
        try:
            return {"yaml": service.read_yaml("skill", skill_id)}
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @app.get("/api/archetypes/{archetype_id}/yaml")
    async def get_archetype_yaml(archetype_id: str) -> dict[str, str]:
        try:
            return {"yaml": service.read_yaml("archetype", archetype_id)}
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @app.get("/api/archetypes/{archetype_id}")
    async def get_archetype_detail(archetype_id: str, request: Request) -> dict[str, Any]:
        try:
            return service.archetype_detail(_get_runtime(request), archetype_id)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @app.get("/api/skills/{skill_id}")
    async def get_skill_detail(skill_id: str, request: Request) -> dict[str, Any]:
        try:
            return service.skill_detail(_get_runtime(request), skill_id)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    # ---- Write endpoints ----

    @app.put("/api/topologies/{topology_id}")
    async def put_topology(topology_id: str, request: Request) -> dict[str, Any]:
        return await _put(request, service, "topology", topology_id, parse_check=True)

    @app.post("/api/topologies")
    async def create_topology(request: Request) -> dict[str, Any]:
        body = await request.json()
        result, new_rt = service.create_from_yaml("topology", body.get("yaml", ""))
        _install(request, new_rt)
        return result

    @app.delete("/api/topologies/{topology_id}")
    async def delete_topology(topology_id: str, request: Request) -> dict[str, Any]:
        try:
            result, new_rt = service.delete("topology", topology_id)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        _install(request, new_rt)
        return result

    @app.put("/api/skills/{skill_id}")
    async def put_skill(skill_id: str, request: Request) -> dict[str, Any]:
        return await _put(request, service, "skill", skill_id, parse_check=False)

    @app.put("/api/archetypes/{archetype_id}")
    async def put_archetype(archetype_id: str, request: Request) -> dict[str, Any]:
        return await _put(request, service, "archetype", archetype_id, parse_check=False)

    @app.post("/api/reload")
    async def reload_workspace(request: Request) -> dict[str, Any]:
        _install(request, service.reload())
        return service.validate_workspace()
