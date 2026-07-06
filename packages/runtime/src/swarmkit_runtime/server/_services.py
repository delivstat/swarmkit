"""Runtime server service layer — ``JobService`` + ``ArtifactService``.

The business logic behind the run/webhook and artifact-CRUD routes, lifted out of the route
closures so it is unit-testable without HTTP — and so the run/webhook path (canary resolution,
capacity check, job creation + persistence + background start) lives in one place instead of being
duplicated across the two handlers.

A service owns no HTTP: it takes plain arguments (the route reads app-state — the live runtime,
canary router, store, semaphore — and passes them in) and raises :class:`ServiceError` subclasses
whose ``status`` the route maps to a code. ``ArtifactService`` mutation methods return the rebuilt
``WorkspaceRuntime`` (or ``None``) for the route to install on ``app.state`` — the one HTTP concern
that stays in the route.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.persistence import SqliteStore
from swarmkit_runtime.resolver import resolve_workspace

from ._config import ServerCfg
from ._jobs import Job, JobStore, _start_job

logger = logging.getLogger("swarmkit.server")

_ARTIFACT_DIRS = {"topology": "topologies", "skill": "skills", "archetype": "archetypes"}


class ServiceError(Exception):
    """Base for server service domain errors. ``status`` is the HTTP code the route maps it to."""

    status = 400


class NotFoundError(ServiceError):
    status = 404


class BusyError(ServiceError):
    status = 429


class JobService:
    """Start topology runs as background jobs — the shared logic behind /run and /hooks.

    The route reads the per-request app-state (live runtime, canary router, store, semaphore,
    server config) and passes it in; the service owns canary resolution, the capacity gate, and
    job creation + persistence + background start.
    """

    def __init__(self, job_store: JobStore) -> None:
        self._jobs = job_store

    def resolve_topology(
        self, rt: WorkspaceRuntime, canary: CanaryRouter | None, topology_name: str
    ) -> tuple[str, str | None]:
        """Resolve a request name to the topology to run + the canary version (if any).

        Raises :class:`NotFoundError` if neither the canary-resolved name nor the bare name exists.
        """
        resolved_name = topology_name
        selected_version: str | None = None
        if canary and canary.has_route(topology_name):
            selected_version = canary.select(topology_name)
            resolved_name = f"{topology_name}@{selected_version}"
        if resolved_name not in rt.workspace.topologies:
            if topology_name not in rt.workspace.topologies:
                available = sorted(rt.workspace.topologies.keys())
                raise NotFoundError(f"Topology '{topology_name}' not found. Available: {available}")
            resolved_name = topology_name
            selected_version = None
        return resolved_name, selected_version

    async def start(
        self,
        *,
        rt: WorkspaceRuntime,
        canary: CanaryRouter | None,
        store: SqliteStore | None,
        cfg: ServerCfg,
        semaphore: Any,
        topology_name: str,
        user_input: str,
        max_steps: int,
    ) -> Job:
        """Resolve, gate on capacity, create + persist the job, and start it in the background."""
        resolved_name, selected_version = self.resolve_topology(rt, canary, topology_name)
        if semaphore is not None and semaphore.locked():
            raise BusyError("Max concurrent jobs reached. Try again later.")
        job = await self._jobs.create(resolved_name, user_input)
        job.version = selected_version
        if store:
            store.create_job(job.id, resolved_name, user_input)
            if selected_version:
                store.update_job(job.id, version=selected_version)
        _start_job(
            self._jobs,
            job,
            rt,
            max_steps,
            timeout_seconds=cfg.timeout_seconds,
            semaphore=semaphore,
            canary_router=canary,
            store=store,
        )
        return job


class ArtifactService:
    """Topology/skill/archetype YAML editing — find/read/write/validate/reload + read projections.

    Mutating methods return ``(result, new_runtime)``: the JSON result the route returns, and the
    rebuilt ``WorkspaceRuntime`` (or ``None`` when no reload was warranted / it failed) for the
    route to install on ``app.state.runtime``.
    """

    def __init__(self, workspace_path: Path) -> None:
        self._ws = workspace_path

    def _artifact_dir(self, kind: str) -> Path:
        return self._ws / _ARTIFACT_DIRS[kind]

    def find_file(self, kind: str, artifact_id: str) -> Path | None:
        d = self._artifact_dir(kind)
        if not d.is_dir():
            return None
        for f in d.rglob("*.yaml"):
            try:
                raw = yaml.safe_load(f.read_text()) or {}
                meta = raw.get("metadata", {})
                if artifact_id in (meta.get("id", ""), meta.get("name", "")):
                    return f
            except Exception:
                continue
        return None

    def read_yaml(self, kind: str, artifact_id: str) -> str:
        f = self.find_file(kind, artifact_id)
        if f is None:
            raise NotFoundError(f"{kind.capitalize()} '{artifact_id}' not found")
        return f.read_text()

    def validate_workspace(self) -> dict[str, Any]:
        try:
            ws = resolve_workspace(self._ws)
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

    def reload(self) -> WorkspaceRuntime | None:
        """Rebuild the runtime from disk. Returns the new runtime, or None on failure (logged)."""
        try:
            return WorkspaceRuntime.from_workspace_path(self._ws)
        except Exception:
            logger.warning("Runtime reload failed after CRUD write", exc_info=True)
            return None

    def put_yaml(
        self, kind: str, artifact_id: str, yaml_content: str, *, dry_run: bool, parse_check: bool
    ) -> tuple[dict[str, Any], WorkspaceRuntime | None]:
        """Write (unless dry-run) then validate; reload only when valid and not a dry-run.

        *parse_check* short-circuits a YAML parse error into a structured result before touching
        disk (the topology editor pre-flights this; skills/archetypes rely on workspace validation).
        """
        if parse_check:
            try:
                yaml.safe_load(yaml_content)
            except Exception as exc:
                return {
                    "valid": False,
                    "errors": [{"code": "yaml.parse", "message": str(exc)}],
                }, None
        if not dry_run:
            f = self.find_file(kind, artifact_id) or (
                self._artifact_dir(kind) / f"{artifact_id}.yaml"
            )
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(yaml_content)
        result = self.validate_workspace()
        new_rt = self.reload() if (result["valid"] and not dry_run) else None
        return result, new_rt

    def create_from_yaml(
        self, kind: str, yaml_content: str
    ) -> tuple[dict[str, Any], WorkspaceRuntime | None]:
        """Create a new artifact file from its metadata.name; reject a name clash / missing name."""
        try:
            parsed = yaml.safe_load(yaml_content)
            name = parsed.get("metadata", {}).get("name", "")
        except Exception as exc:
            return {"valid": False, "errors": [{"code": "yaml.parse", "message": str(exc)}]}, None
        if not name:
            return {
                "valid": False,
                "errors": [{"code": "missing.name", "message": "metadata.name required"}],
            }, None
        f = self._artifact_dir(kind) / f"{name}.yaml"
        if f.exists():
            return {
                "valid": False,
                "errors": [{"code": "exists", "message": f"'{name}' already exists"}],
            }, None
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(yaml_content)
        result = self.validate_workspace()
        new_rt = self.reload() if result["valid"] else None
        return result, new_rt

    def delete(self, kind: str, artifact_id: str) -> tuple[dict[str, Any], WorkspaceRuntime | None]:
        f = self.find_file(kind, artifact_id)
        if f is None:
            raise NotFoundError(f"{kind.capitalize()} '{artifact_id}' not found")
        f.unlink()
        return {"deleted": True, "id": artifact_id}, self.reload()

    # ---- read projections over the live runtime -----------------------------

    def topology_detail(self, rt: WorkspaceRuntime, topology_id: str) -> dict[str, Any]:
        topo = rt.workspace.topologies.get(topology_id)
        if topo is None:
            raise NotFoundError(f"Topology '{topology_id}' not found")

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

    def archetype_detail(self, rt: WorkspaceRuntime, archetype_id: str) -> dict[str, Any]:
        arch = rt.workspace.archetypes.get(archetype_id)
        if arch is None:
            raise NotFoundError(f"Archetype '{archetype_id}' not found")
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

    def skill_detail(self, rt: WorkspaceRuntime, skill_id: str) -> dict[str, Any]:
        skill = rt.workspace.skills.get(skill_id)
        if skill is None:
            raise NotFoundError(f"Skill '{skill_id}' not found")
        raw = skill.raw
        impl = getattr(raw, "implementation", None)
        return {
            "id": skill_id,
            "name": raw.metadata.name,
            "description": getattr(raw.metadata, "description", ""),
            "category": getattr(raw.category, "value", str(raw.category)),
            "implementation_type": getattr(impl, "type", None) if impl else None,
        }
