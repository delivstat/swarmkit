"""CRUD endpoints for topology / skill / archetype YAML editing (authoring surface)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.errors import ResolutionErrors

from ._helpers import (
    _get_runtime,
)

logger = logging.getLogger("swarmkit.server")


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
