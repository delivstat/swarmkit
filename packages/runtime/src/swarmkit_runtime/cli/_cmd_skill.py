"""``swarmkit skill`` — find, add, import, check and remove skills (skill-registry.md).

Thin over ``swarmkit_runtime.skills._registry``: the CLI renders and asks; the service decides and
writes. ``add`` shows what it would write to two places and asks before touching ``workspace.yaml``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import typer

from ._app import skill_app

_WS = Annotated[Path, typer.Option("--workspace", "-w", help="Workspace root (default: .).")]
_JSON = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


def _fail(msg: str, code: int = 1) -> None:
    typer.echo(f"error: {msg}", err=True)
    raise typer.Exit(code=code)


def _catalogue(refresh: bool) -> Any:
    from swarmkit_runtime.skills._registry import (  # noqa: PLC0415
        SkillRegistryError,
        load_catalogue,
    )

    try:
        return load_catalogue(refresh=refresh)
    except SkillRegistryError as exc:
        _fail(str(exc))


@skill_app.command("list")
def list_skills(
    workspace: _WS = Path("."),
    available: Annotated[
        bool, typer.Option("--available", help="The catalogue instead of the workspace.")
    ] = False,
    refresh: Annotated[bool, typer.Option("--refresh", help="Refetch the catalogue.")] = False,
    as_json: _JSON = False,
) -> None:
    """The workspace's skills — or, with --available, the catalogue's."""
    if available:
        cat = _catalogue(refresh)
        if as_json:
            typer.echo(
                json.dumps(
                    {
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
                            for b in cat.bundles.values()
                        ],
                    },
                    indent=2,
                )
            )
            return
        typer.echo(f"# catalogue: {cat.source}")
        for b in sorted(cat.bundles.values(), key=lambda b: b.id):
            v = b.verification
            when = f"{v.get('state', '?')} {v.get('checked_at', '')}".strip()
            typer.echo(f"{b.id:<22} {when:<22} {b.description}")
            for sid in b.skill_ids:
                s = cat.skills.get(sid)
                desc = (s.description if s else "").split("\n")[0][:70]
                typer.echo(f"  {sid:<20} {desc}")
        return

    from swarmkit_runtime.errors import ResolutionErrors  # noqa: PLC0415
    from swarmkit_runtime.skills._registry import workspace_skills  # noqa: PLC0415

    try:
        rows = workspace_skills(workspace)
    except ResolutionErrors as exc:
        _fail(f"workspace does not resolve: {exc.errors[0].message}")
    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "id": r.id,
                        "name": r.name,
                        "category": r.category,
                        "backing": r.backing,
                        "server": r.server_id,
                        "path": str(r.path.relative_to(workspace)),
                        "held_by": list(r.held_by),
                    }
                    for r in rows
                ],
                indent=2,
            )
        )
        return
    if not rows:
        typer.echo("no skill files in this workspace (skills/ is empty).")
        return
    for r in rows:
        where = f"{r.backing}" + (f" → {r.server_id}" if r.server_id else "")
        held = ", ".join(r.held_by) if r.held_by else "held by nobody"
        typer.echo(f"{r.id:<24} {r.category:<12} {where:<28} {held}")


@skill_app.command("search")
def search(
    query: Annotated[str, typer.Argument(help="Words to look for in ids, names, descriptions.")],
    workspace: _WS = Path("."),
    refresh: Annotated[bool, typer.Option("--refresh", help="Refetch the catalogue.")] = False,
    as_json: _JSON = False,
) -> None:
    """Search the catalogue (and this workspace's own skills)."""
    from swarmkit_runtime.skills._registry import workspace_skills  # noqa: PLC0415

    cat = _catalogue(refresh)
    hits = cat.search(query)
    words = query.lower().split()
    local: list[Any] = []
    try:
        local = [
            r
            for r in workspace_skills(workspace)
            if all(w in f"{r.id} {r.name}".lower() for w in words)
        ]
    except Exception:  # a workspace that does not resolve is not what search is for
        local = []
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "catalogue": [
                        {
                            "id": s.id,
                            "bundle": s.bundle,
                            "name": s.name,
                            "description": s.description,
                            "backing": s.backing,
                        }
                        for s in hits
                    ],
                    "workspace": [r.id for r in local],
                },
                indent=2,
            )
        )
        return
    if not hits and not local:
        typer.echo(f"nothing in the catalogue or this workspace matches {query!r}.")
        return
    for s in hits:
        installed = " (installed)" if any(r.id == s.id for r in local) else ""
        typer.echo(f"{s.id:<24} {s.bundle:<14} {s.description.split(chr(10))[0][:70]}{installed}")
    for r in local:
        if not any(s.id == r.id for s in hits):
            typer.echo(f"{r.id:<24} {'(workspace)':<14} {r.name}")


@skill_app.command("show")
def show(
    skill_id: Annotated[str, typer.Argument(help="A skill or bundle id.")],
    workspace: _WS = Path("."),
    refresh: Annotated[bool, typer.Option("--refresh", help="Refetch the catalogue.")] = False,
) -> None:
    """One skill (or bundle), from the workspace if it has it, else the catalogue."""
    import yaml  # noqa: PLC0415

    local = workspace / "skills" / f"{skill_id}.yaml"
    if local.exists():
        typer.echo(f"# {local}")
        typer.echo(local.read_text())
        return
    cat = _catalogue(refresh)
    if skill_id in cat.bundles:
        b = cat.bundles[skill_id]
        typer.echo(f"# catalogue bundle {b.id}")
        typer.echo(yaml.safe_dump(b.raw, sort_keys=False))
        return
    if skill_id in cat.skills:
        s = cat.skills[skill_id]
        typer.echo(f"# catalogue skill {s.id} (bundle {s.bundle})")
        typer.echo(yaml.safe_dump(s.raw, sort_keys=False))
        return
    close = cat.suggest(skill_id)
    _fail(
        f"'{skill_id}' is neither here nor in the catalogue"
        + (f" — did you mean {', '.join(close)}?" if close else "")
    )


@skill_app.command("add")
def add(
    ref: Annotated[
        str, typer.Argument(help="A catalogue skill or bundle id, or a Skill/SkillBundle file/URL.")
    ],
    workspace: _WS = Path("."),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print what would be written; write nothing.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before writing.")] = False,
    refresh: Annotated[bool, typer.Option("--refresh", help="Refetch the catalogue.")] = False,
) -> None:
    """Add a skill (and the MCP server it needs) to this workspace."""
    from swarmkit_runtime.skills._registry import (  # noqa: PLC0415
        SkillRegistryError,
        apply_add,
        plan_add,
    )

    cat = None
    if not (ref.startswith(("http://", "https://")) or "/" in ref or ref.endswith(".yaml")):
        cat = _catalogue(refresh)
    try:
        plan = plan_add(workspace, ref, catalogue=cat)
    except SkillRegistryError as exc:
        _fail(str(exc))
    for note in plan.notes:
        typer.echo(f"# {note}")
    for path, text in plan.skill_files.items():
        state = (
            "unchanged"
            if path.exists() and path.read_text() == text
            else ("overwrite" if path.exists() else "new")
        )
        typer.echo(f"# {path.relative_to(workspace)} ({state})")
        typer.echo(text.rstrip())
        typer.echo()
    if plan.server_entry is not None:
        state = {
            "new": "added to workspace.yaml",
            "same": "already in workspace.yaml, unchanged",
            "differs": "REPLACES the entry in workspace.yaml",
        }[plan.server_state]
        typer.echo(f"# mcp_servers entry ({state})")
        typer.echo(plan.server_fragment.rstrip())
        typer.echo()
    if dry_run:
        typer.echo("dry run — nothing written.")
        return
    if plan.server_entry is not None and plan.server_state != "same" and not yes:
        what = f"write {len(plan.skill_files)} skill file(s) and edit workspace.yaml"
        if not typer.confirm(f"{what}?", default=True):
            typer.echo("nothing written.")
            raise typer.Exit(code=0)
    elif not yes and not typer.confirm(
        f"write {len(plan.skill_files)} skill file(s)?", default=True
    ):
        typer.echo("nothing written.")
        raise typer.Exit(code=0)
    try:
        result = apply_add(workspace, plan)
    except SkillRegistryError as exc:
        _fail(str(exc))
    files = ", ".join(result["skill_files"]) or "no new files"
    server = " and mcp_servers updated" if result["server"] else ""
    typer.echo(f"wrote {files}{server}.")


@skill_app.command("import")
def import_(
    ref: Annotated[str, typer.Argument(help="A SKILL.md file, its directory, or a URL.")],
    workspace: _WS = Path("."),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the skill; write nothing.")
    ] = False,
) -> None:
    """Import an Agent Skills SKILL.md as an llm_prompt skill."""
    import yaml  # noqa: PLC0415

    from swarmkit_runtime.skills._registry import (  # noqa: PLC0415
        SkillRegistryError,
        convert_skill_md,
        import_skill_md,
    )

    try:
        if dry_run:
            p = Path(ref)
            if p.is_dir():
                p = p / "SKILL.md"
            text = p.read_text() if not ref.startswith("http") else _fetch(ref)
            raw = convert_skill_md(text, ref)
            typer.echo(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
            typer.echo("dry run — nothing written.")
            return
        path, raw = import_skill_md(workspace, ref)
    except (SkillRegistryError, OSError) as exc:
        _fail(str(exc))
    typer.echo(f"wrote {path.relative_to(workspace)} ({raw['metadata']['id']}, llm_prompt).")


def _fetch(url: str) -> str:
    import httpx  # noqa: PLC0415

    resp = httpx.get(url, timeout=20, follow_redirects=True)
    if resp.status_code != 200:
        _fail(f"GET {url} returned {resp.status_code}")
    return resp.text


@skill_app.command("check")
def check(workspace: _WS = Path("."), as_json: _JSON = False) -> None:
    """Start each mcp_tool skill's server and ask whether its tool still exists."""
    from swarmkit_runtime.skills._registry import check_skills  # noqa: PLC0415

    results = asyncio.run(check_skills(workspace))
    if as_json:
        typer.echo(json.dumps([r.__dict__ for r in results], indent=2))
    elif not results:
        typer.echo("no mcp_tool skills to check.")
    for r in results if not as_json else []:
        mark = {"ok": "ok     ", "missing": "MISSING", "server-failed": "FAILED "}[r.status]
        detail = f" — {r.detail}" if r.detail else ""
        typer.echo(f"{mark} {r.skill_id:<24} {r.server_id}:{r.tool}{detail}")
    if any(r.status != "ok" for r in results):
        raise typer.Exit(code=1)


@skill_app.command("remove")
def remove(
    skill_id: Annotated[str, typer.Argument(help="The skill's id.")],
    workspace: _WS = Path("."),
) -> None:
    """Delete skills/<id>.yaml — refused while an archetype or agent holds the skill."""
    from swarmkit_runtime.skills._registry import SkillRegistryError, remove_skill  # noqa: PLC0415

    try:
        path = remove_skill(workspace, skill_id)
    except SkillRegistryError as exc:
        _fail(str(exc))
    typer.echo(f"removed {path.relative_to(workspace)}.")
