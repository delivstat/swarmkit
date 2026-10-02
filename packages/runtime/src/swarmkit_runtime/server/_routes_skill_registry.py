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


def _register_skill_registry_routes(app: FastAPI) -> None:  # noqa: PLR0915
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
                "server_fragment": plan.server_fragment,
                "server_state": plan.server_state,
                "notes": list(plan.notes),
                "applied": None,
            }
            if not body.dry_run:
                out["applied"] = apply_add(workspace, plan)
        except SkillRegistryError as exc:
            raise HTTPException(400, str(exc)) from exc
        if out["applied"] is not None:
            await _reload(request)
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
            await _reload(request)
        return {"path": str(path.relative_to(workspace)), "skill": raw, "written": not body.dry_run}

    @app.get("/api/skills/check")
    async def skills_check(request: Request) -> list[dict[str, Any]]:
        """Are the tools the workspace's mcp_tool skills name still there?"""
        from swarmkit_runtime.skills._registry import check_skills  # noqa: PLC0415

        return [r.__dict__ for r in await check_skills(request.app.state.workspace_path)]

    @app.post("/api/skills/{skill_id}/activate")
    async def activate_skill(skill_id: str, request: Request) -> dict[str, Any]:
        """Preflight check: can the signed-in caller run this skill right now?

        Returns 200 when every ``requires_credentials`` entry has a token in the OAuth store
        for the caller. Returns 409 ``missing_credentials`` with a per-credential pointer to
        the setup topology (``google-workspace-setup`` for Google-shaped issuers; GitHub /
        Notion / etc. follow the same shape) when any required credential is missing.

        See design/details/skill-requires-credentials.md §Activation refusal. The route changes
        no state — nothing is "activated" server-side; the name reflects the UI question
        ("activate this skill?") rather than a store write. A 200 means the skill is runnable
        for THIS caller right now; a 409 names what the caller needs to connect.

        Shape:

        ```
        200 { "ok": true, "skill_id": "...", "requires_credentials": [...] }
        409 { "error": "missing_credentials",
              "missing": [{ "credential_id": "...", "issuer": "...",
                            "setup_topology": "google-workspace-setup" }] }
        404 { "detail": "skill_not_found: ..." }
        ```
        """
        from pathlib import Path  # noqa: PLC0415

        from swarmkit_runtime.oauth._store import TokenStore  # noqa: PLC0415
        from swarmkit_runtime.resolver import resolve_workspace  # noqa: PLC0415
        from swarmkit_runtime.skills._registry import workspace_skills  # noqa: PLC0415

        workspace_path = request.app.state.workspace_path
        assert isinstance(workspace_path, Path)

        skills = workspace_skills(workspace_path)
        skill = next((s for s in skills if s.id == skill_id), None)
        if skill is None:
            raise HTTPException(404, f"skill_not_found: {skill_id}")

        requires = list(skill.requires_credentials)
        if not requires:
            return {"ok": True, "skill_id": skill_id, "requires_credentials": []}

        owner = _owner_of_request(request)
        ws = resolve_workspace(workspace_path)
        store = TokenStore(workspace_path)
        credentials_block = dict(getattr(ws.raw, "credentials", None) or {})
        missing: list[dict[str, Any]] = []
        for credential_id in requires:
            cred_entry = credentials_block.get(credential_id)
            endpoint = _credential_endpoint(cred_entry)
            if store.metadata(credential_id, owner) is None:
                missing.append(_missing_entry(credential_id, endpoint))

        if missing:
            raise HTTPException(
                status_code=409,
                detail={"error": "missing_credentials", "missing": missing},
            )
        return {
            "ok": True,
            "skill_id": skill_id,
            "requires_credentials": requires,
        }


async def _reload(request: Request) -> None:
    """Pick the new file(s) up the way a CRUD write does, so the Skills page sees them."""
    from ._helpers import swap_runtime  # noqa: PLC0415
    from ._services import ArtifactService  # noqa: PLC0415

    await swap_runtime(request.app, ArtifactService(request.app.state.workspace_path).reload())


#: Known OAuth authorization servers (`issuer`) → setup topology that connects them. Hand-maintained
#: for providers whose MCP servers do not support dynamic client registration — the ones where
#: #1000 part 2 (`google-workspace-setup`) and its siblings do the GCP-console walkthrough. New
#: providers extend this map alongside their own `<provider>-setup` reference workspace.
SETUP_TOPOLOGIES: dict[str, str] = {
    "https://accounts.google.com": "google-workspace-setup",
}


def _owner_of_request(request: Request) -> str:
    """The signed-in caller's identity — same resolution as the OAuth routes use."""
    identity = getattr(request.state, "identity", None)
    return str(getattr(identity, "client_id", "") or "local")


def _credential_endpoint(entry: Any) -> str | None:
    """Walk the workspace's ``credentials.<id>.config.endpoint`` without caring whether the
    resolver returned a pydantic model or a dict."""
    if entry is None:
        return None
    config = getattr(entry, "config", None)
    if config is None and hasattr(entry, "model_dump"):
        config = entry.model_dump(mode="json").get("config")
    if config is None and isinstance(entry, dict):
        config = entry.get("config")
    if config is None:
        return None
    if hasattr(config, "endpoint"):
        return str(config.endpoint) if config.endpoint else None
    if isinstance(config, dict):
        return str(config.get("endpoint")) if config.get("endpoint") else None
    return None


def _missing_entry(credential_id: str, endpoint: str | None) -> dict[str, Any]:
    """One row of the 409 payload — identifies the credential, the issuer it belongs to, and
    the setup topology that will register it."""
    issuer = _issuer_from_endpoint(endpoint) if endpoint else None
    row: dict[str, Any] = {"credential_id": credential_id}
    if issuer:
        row["issuer"] = issuer
        setup = SETUP_TOPOLOGIES.get(issuer)
        if setup:
            row["setup_topology"] = setup
    return row


def _issuer_from_endpoint(endpoint: str) -> str | None:
    """Best-effort issuer resolution without hitting the network — the endpoint URL's origin
    normalised to the OAuth issuer the catalogue bundles use. Google's remote MCP endpoints all
    issue through ``https://accounts.google.com``.
    """
    if "googleapis.com" in endpoint:
        return "https://accounts.google.com"
    return None
