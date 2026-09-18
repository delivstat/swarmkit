"""``/api/skill-catalogue`` and ``/api/skills/{add,import,check}`` — the `swarmkit skill` service
over HTTP (design/details/skill-registry.md), so the portal is a peer of the CLI. Thin: every
decision is in ``swarmkit_runtime.skills._registry``. Writes are ``/api/*`` and therefore admin.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ._helpers import _get_runtime


class AddRequest(BaseModel):
    ref: str
    dry_run: bool = False


class ImportRequest(BaseModel):
    text: str  # the SKILL.md content
    origin: str = "upload"
    dry_run: bool = False


def _register_skill_registry_routes(app: FastAPI) -> None:
    @app.get("/api/skill-catalogue")
    async def skill_catalogue(
        request: Request, q: str = "", refresh: bool = False
    ) -> dict[str, Any]:
        """The catalogue's bundles and skills — filtered by *q* when given."""
        from swarmkit_runtime.skills._registry import (  # noqa: PLC0415
            SkillRegistryError,
            load_catalogue,
        )

        try:
            cat = load_catalogue(refresh=refresh)
        except SkillRegistryError as exc:
            raise HTTPException(502, str(exc)) from exc
        installed = set(_get_runtime(request).workspace.skills)
        skills = cat.search(q) if q else sorted(cat.skills.values(), key=lambda s: s.id)
        return {
            "source": cat.source,
            "bundles": [
                {
                    "id": b.id,
                    "name": b.name,
                    "description": b.description,
                    "skills": list(b.skill_ids),
                    "verification": b.verification,
                    "requires_runtime": b.requires_runtime,
                }
                for b in sorted(cat.bundles.values(), key=lambda b: b.id)
            ],
            "skills": [
                {
                    "id": s.id,
                    "bundle": s.bundle,
                    "name": s.name,
                    "description": s.description,
                    "backing": s.backing,
                    "server": s.server_id,
                    "installed": s.id in installed,
                }
                for s in skills
            ],
        }

    @app.post("/api/skills/add")
    async def skills_add(request: Request, body: AddRequest) -> dict[str, Any]:
        """Plan (and unless dry_run, apply) adding a catalogue skill/bundle or a Skill file."""
        from swarmkit_runtime.skills._registry import (  # noqa: PLC0415
            SkillRegistryError,
            apply_add,
            plan_add,
        )

        workspace = request.app.state.workspace_path
        try:
            plan = plan_add(workspace, body.ref)
            out: dict[str, Any] = {
                "skill_files": {
                    str(p.relative_to(workspace)): t for p, t in plan.skill_files.items()
                },
                "server_entry": plan.server_entry,
                "server_state": plan.server_state,
                "notes": list(plan.notes),
                "applied": None,
            }
            if not body.dry_run:
                out["applied"] = apply_add(workspace, plan)
        except SkillRegistryError as exc:
            raise HTTPException(400, str(exc)) from exc
        if out["applied"] is not None:
            _reload(request)
        return out

    @app.post("/api/skills/import")
    async def skills_import(request: Request, body: ImportRequest) -> dict[str, Any]:
        """Convert a SKILL.md (given as text) and, unless dry_run, write it."""
        from swarmkit_runtime.skills._registry import (  # noqa: PLC0415
            SkillRegistryError,
            _dump,
            convert_skill_md,
        )

        workspace = request.app.state.workspace_path
        try:
            raw = convert_skill_md(body.text, body.origin)
        except SkillRegistryError as exc:
            raise HTTPException(400, str(exc)) from exc
        path = workspace / "skills" / f"{raw['metadata']['id']}.yaml"
        if not body.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_dump(raw))
            _reload(request)
        return {"path": str(path.relative_to(workspace)), "skill": raw, "written": not body.dry_run}

    @app.get("/api/skills/check")
    async def skills_check(request: Request) -> list[dict[str, Any]]:
        """Are the tools the workspace's mcp_tool skills name still there?"""
        from swarmkit_runtime.skills._registry import check_skills  # noqa: PLC0415

        return [r.__dict__ for r in await check_skills(request.app.state.workspace_path)]


def _reload(request: Request) -> None:
    """Pick the new file(s) up the way a CRUD write does, so the Skills page sees them."""
    from ._routes_crud import _install  # noqa: PLC0415
    from ._services import ArtifactService  # noqa: PLC0415

    _install(request, ArtifactService(request.app.state.workspace_path).reload())
