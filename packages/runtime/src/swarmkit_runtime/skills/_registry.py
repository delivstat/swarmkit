"""The `swarmkit skill` service — find, add, import and check skills (skill-registry.md).

Three sources, in order: the workspace's own ``skills/``; the ``swarmkit-skills`` catalogue
(``kind: SkillBundle`` entries, each a server block plus the skills that use it, verified nightly
by the catalogue's own CI); and a Skill or SkillBundle file given by path or URL.

Adding a catalogue skill writes to two places — a new file under ``skills/`` and an ``mcp_servers``
entry in ``workspace.yaml`` — so the planner produces both fragments first and the applier writes
them only when told to; the workspace edit goes through the same comment-preserving,
validate-or-roll-back service the portal uses. Nothing here reaches the network without being
asked: the catalogue index is cached for a day and a local directory can stand in for it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from swarmkit_runtime._versions import runtime_version
from swarmkit_runtime.skills._runtime_floor import _parse as _parse_floor
from swarmkit_runtime.skills._runtime_floor import _version_tuple

logger = logging.getLogger("swarmkit.skills.registry")

#: The catalogue. A directory (a checkout, or a fixture) or an ``https://`` raw base with the same
#: layout — ``skills/<bundle>/bundle.yaml`` and ``skills/<bundle>/skills/<id>.yaml``.
DEFAULT_CATALOGUE = "https://raw.githubusercontent.com/delivstat/swarmkit-skills/main"
CATALOGUE_ENV = "SWARMKIT_SKILLS_CATALOGUE"
#: The GitHub contents API for the bundle listing (raw hosts serve files, not directories).
_GITHUB_CONTENTS = "https://api.github.com/repos/delivstat/swarmkit-skills/contents/skills"
CACHE_TTL_S = 24 * 3600


class SkillRegistryError(Exception):
    """A registry operation that could not complete; the message is for the person."""


# ---- the catalogue --------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogueSkill:
    id: str
    name: str
    description: str
    bundle: str
    category: str
    backing: str
    server_id: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class CatalogueBundle:
    id: str
    name: str
    description: str
    server: dict[str, Any] | None
    skill_ids: tuple[str, ...]
    requires_runtime: str | None
    verification: dict[str, Any]
    raw: dict[str, Any]


@dataclass
class Catalogue:
    source: str
    fetched_at: float
    bundles: dict[str, CatalogueBundle] = field(default_factory=dict)
    skills: dict[str, CatalogueSkill] = field(default_factory=dict)

    def search(self, query: str) -> list[CatalogueSkill]:
        """Skills whose id, name, description or bundle mention every word of *query*, best first
        — an id/name hit outranks a description hit."""
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w]
        if not words:
            return sorted(self.skills.values(), key=lambda s: s.id)
        scored: list[tuple[int, CatalogueSkill]] = []
        for s in self.skills.values():
            head = f"{s.id} {s.name} {s.bundle}".lower()
            body = s.description.lower()
            score = 0
            for w in words:
                if w in head:
                    score += 3
                elif w in body:
                    score += 1
                else:
                    score = 0
                    break
            if score:
                scored.append((score, s))
        return [s for _, s in sorted(scored, key=lambda t: (-t[0], t[1].id))]

    def suggest(self, wanted: str, limit: int = 3) -> list[str]:
        """The closest ids to *wanted*, for an error message that helps."""
        import difflib  # noqa: PLC0415

        pool = list(self.skills) + list(self.bundles)
        return difflib.get_close_matches(wanted, pool, n=limit, cutoff=0.5)


def _cache_path() -> Path:
    return Path.home() / ".swarmkit" / "cache" / "skills-catalogue.json"


def catalogue_source() -> str:
    return os.environ.get(CATALOGUE_ENV, "").strip() or DEFAULT_CATALOGUE


def _is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def _read_source(source: str, rel: str, client: httpx.Client | None) -> str:
    if _is_url(source):
        if client is None:
            raise SkillRegistryError("no HTTP client for a remote catalogue")
        resp = client.get(f"{source.rstrip('/')}/{rel}")
        if resp.status_code != 200:
            raise SkillRegistryError(f"catalogue: GET {rel} returned {resp.status_code}")
        return resp.text
    return (Path(source) / rel).read_text()


def _list_bundles(source: str, client: httpx.Client | None) -> list[str]:
    if _is_url(source):
        if client is None:
            raise SkillRegistryError("no HTTP client for a remote catalogue")
        # The default catalogue is a GitHub repo: its directory listing is the contents API.
        if "githubusercontent.com/delivstat/swarmkit-skills" in source:
            resp = client.get(_GITHUB_CONTENTS)
            if resp.status_code != 200:
                raise SkillRegistryError(
                    f"catalogue: listing bundles returned {resp.status_code} — "
                    "try again later, or point SWARMKIT_SKILLS_CATALOGUE at a checkout"
                )
            return sorted(e["name"] for e in resp.json() if e.get("type") == "dir")
        # Any other mirror: an index.json of bundle ids at its root.
        resp = client.get(f"{source.rstrip('/')}/index.json")
        if resp.status_code != 200:
            raise SkillRegistryError(f"catalogue: {source} has no index.json")
        body = resp.json()
        return sorted(body if isinstance(body, list) else body.get("bundles", []))
    root = Path(source) / "skills"
    if not root.is_dir():
        raise SkillRegistryError(f"catalogue: {root} is not a directory")
    return sorted(p.name for p in root.iterdir() if (p / "bundle.yaml").exists())


def _build(source: str, client: httpx.Client | None) -> Catalogue:
    cat = Catalogue(source=source, fetched_at=time.time())
    for bundle_id in _list_bundles(source, client):
        try:
            braw = yaml.safe_load(_read_source(source, f"skills/{bundle_id}/bundle.yaml", client))
        except (OSError, yaml.YAMLError, SkillRegistryError) as exc:
            logger.warning("catalogue: skipping bundle %s: %s", bundle_id, exc)
            continue
        if not isinstance(braw, dict):
            continue
        meta = braw.get("metadata") or {}
        bundle = CatalogueBundle(
            id=str(meta.get("id") or bundle_id),
            name=str(meta.get("name") or bundle_id),
            description=str(meta.get("description") or ""),
            server=braw.get("server") if isinstance(braw.get("server"), dict) else None,
            skill_ids=tuple(str(s) for s in braw.get("skills") or []),
            requires_runtime=(
                str(braw["requires_runtime"]) if braw.get("requires_runtime") else None
            ),
            verification=dict(braw.get("verification") or {}),
            raw=braw,
        )
        cat.bundles[bundle.id] = bundle
        for sid in bundle.skill_ids:
            try:
                sraw = yaml.safe_load(
                    _read_source(source, f"skills/{bundle_id}/skills/{sid}.yaml", client)
                )
            except (OSError, yaml.YAMLError, SkillRegistryError) as exc:
                logger.warning("catalogue: skipping skill %s/%s: %s", bundle_id, sid, exc)
                continue
            if not isinstance(sraw, dict):
                continue
            smeta = sraw.get("metadata") or {}
            impl = sraw.get("implementation") or {}
            cat.skills[sid] = CatalogueSkill(
                id=sid,
                name=str(smeta.get("name") or sid),
                description=str(smeta.get("description") or ""),
                bundle=bundle.id,
                category=str(sraw.get("category") or ""),
                backing=str(impl.get("type") or ""),
                server_id=str(impl.get("server")) if impl.get("server") else None,
                raw=sraw,
            )
    return cat


def _to_json(cat: Catalogue) -> dict[str, Any]:
    return {
        "source": cat.source,
        "fetched_at": cat.fetched_at,
        "bundles": {b.id: b.raw for b in cat.bundles.values()},
        "skills": {s.id: {"bundle": s.bundle, "raw": s.raw} for s in cat.skills.values()},
    }


def _from_json(body: dict[str, Any]) -> Catalogue:
    cat = Catalogue(source=str(body["source"]), fetched_at=float(body["fetched_at"]))
    for bid, braw in (body.get("bundles") or {}).items():
        meta = braw.get("metadata") or {}
        cat.bundles[bid] = CatalogueBundle(
            id=bid,
            name=str(meta.get("name") or bid),
            description=str(meta.get("description") or ""),
            server=braw.get("server") if isinstance(braw.get("server"), dict) else None,
            skill_ids=tuple(str(s) for s in braw.get("skills") or []),
            requires_runtime=(
                str(braw["requires_runtime"]) if braw.get("requires_runtime") else None
            ),
            verification=dict(braw.get("verification") or {}),
            raw=braw,
        )
    for sid, entry in (body.get("skills") or {}).items():
        sraw = entry["raw"]
        smeta = sraw.get("metadata") or {}
        impl = sraw.get("implementation") or {}
        cat.skills[sid] = CatalogueSkill(
            id=sid,
            name=str(smeta.get("name") or sid),
            description=str(smeta.get("description") or ""),
            bundle=str(entry["bundle"]),
            category=str(sraw.get("category") or ""),
            backing=str(impl.get("type") or ""),
            server_id=str(impl.get("server")) if impl.get("server") else None,
            raw=sraw,
        )
    return cat


def load_catalogue(
    *, refresh: bool = False, client: httpx.Client | None = None, cache: Path | None = None
) -> Catalogue:
    """The catalogue: from the day-old cache when the source is remote and *refresh* is not set,
    else fetched (or read from the directory) and cached. A directory source is never cached — it
    is already local and may be edited under a test."""
    source = catalogue_source()
    cache_file = cache or _cache_path()
    remote = _is_url(source)
    if remote and not refresh and cache_file.exists():
        try:
            body = json.loads(cache_file.read_text())
            if (
                body.get("source") == source
                and time.time() - float(body["fetched_at"]) < CACHE_TTL_S
            ):
                return _from_json(body)
        except (OSError, ValueError, KeyError):
            pass
    own_client = None
    if remote and client is None:
        own_client = client = httpx.Client(timeout=20, follow_redirects=True)
    try:
        cat = _build(source, client)
    finally:
        if own_client is not None:
            own_client.close()
    if remote:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(_to_json(cat)))
        except OSError:
            logger.debug("catalogue: could not write cache", exc_info=True)
    return cat


# ---- the workspace side ---------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceSkill:
    id: str
    name: str
    category: str
    backing: str
    server_id: str | None
    path: Path
    held_by: tuple[str, ...]  # "archetype:<id>" and "<topology>/<agent>" that grant it


def _skill_files(workspace: Path) -> list[Path]:
    d = workspace / "skills"
    return sorted(d.rglob("*.yaml")) if d.is_dir() else []


def _holders(ws: Any) -> dict[str, list[str]]:
    """skill id → who grants or binds it: ``archetype:<id>``, ``<topology>/<agent>``,
    ``bound:<trigger>`` (a workspace binding) or ``bound:<topology>``."""
    held: dict[str, list[str]] = {}
    for aid, arch in ws.archetypes.items():
        defaults = getattr(arch.raw, "defaults", None)
        for s in (getattr(defaults, "skills", None) or []) if defaults else []:
            held.setdefault(_sid(s), []).append(f"archetype:{aid}")
    for tid, topo in ws.topologies.items():
        if "@" in tid:
            continue  # a canary alias of a topology already walked under its name
        for agent in _walk_agents(topo):
            for s in getattr(agent, "skills", None) or ():
                held.setdefault(_sid(s), []).append(f"{tid}/{agent.id}")
        tgov = getattr(topo.raw, "governance", None)
        for b in (getattr(tgov, "decision_skills", None) or []) if tgov else []:
            held.setdefault(str(b.id), []).append(f"bound:{tid}")
    # Decision skills are bound, not granted: a workspace binding is how they run.
    gov = getattr(ws.raw, "governance", None)
    for b in (getattr(gov, "decision_skills", None) or []) if gov else []:
        held.setdefault(str(b.id), []).append(f"bound:{getattr(b.trigger, 'value', b.trigger)}")
    return held


def _skill_file_index(workspace: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for f in _skill_files(workspace):
        try:
            raw = yaml.safe_load(f.read_text()) or {}
        except yaml.YAMLError:
            continue
        sid = str((raw.get("metadata") or {}).get("id") or "")
        if sid:
            files[sid] = f
    return files


def workspace_skills(workspace: Path) -> list[WorkspaceSkill]:
    """The workspace's skills as the resolver sees them, with who holds each — the ``held_by``
    answer is what `remove` refuses on and what `list` shows."""
    from swarmkit_runtime.resolver import resolve_workspace  # noqa: PLC0415

    ws = resolve_workspace(workspace)
    held = _holders(ws)
    files = _skill_file_index(workspace)
    out: list[WorkspaceSkill] = []
    for sid, skill in sorted(ws.skills.items()):
        if sid not in files:
            continue  # bundled or synthesized (command packs, topology-as-agent) — not a file here
        impl = skill.raw.implementation
        out.append(
            WorkspaceSkill(
                id=sid,
                name=str(getattr(skill.raw.metadata, "name", "") or sid),
                category=str(getattr(skill.raw.category, "value", None) or skill.raw.category),
                backing=str(_impl_get(impl, "type") or ""),
                server_id=(str(_impl_get(impl, "server")) if _impl_get(impl, "server") else None),
                path=files[sid],
                held_by=tuple(sorted(set(held.get(sid, [])))),
            )
        )
    return out


def _sid(s: Any) -> str:
    return str(getattr(s, "id", None) or s)


def _impl_get(impl: Any, key: str) -> Any:
    if isinstance(impl, dict):
        return impl.get(key)
    return getattr(impl, key, None)


def _walk_agents(topology: Any) -> list[Any]:
    out: list[Any] = []
    stack = [topology.root]
    while stack:
        a = stack.pop()
        out.append(a)
        stack.extend(getattr(a, "children", None) or [])
    return out


# ---- add: plan, then apply --------------------------------------------------------------------


@dataclass(frozen=True)
class AddPlan:
    """What `add` would write: skill files (path → YAML text) and, when a server is involved, the
    ``mcp_servers`` entry to upsert. Produced without touching the workspace."""

    skill_files: dict[Path, str]
    server_entry: dict[str, Any] | None
    #: "new" (not in workspace.yaml), "same" (an identical entry is there), "differs" (an entry
    #: with that id is there and would be replaced).
    server_state: str
    notes: tuple[str, ...]

    @property
    def server_exists(self) -> bool:
        return self.server_state != "new"

    @property
    def server_fragment(self) -> str:
        if self.server_entry is None:
            return ""
        return yaml.safe_dump({"mcp_servers": [self.server_entry]}, sort_keys=False)


def _floor_ok(spec: str | None) -> tuple[bool, str]:
    if not spec:
        return True, ""
    want = _parse_floor(spec)
    have = _version_tuple(runtime_version())
    if want is None or have is None:
        return True, ""
    return have >= want, runtime_version()


def _load_file_or_url(ref: str) -> dict[str, Any]:
    if _is_url(ref):
        resp = httpx.get(ref, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            raise SkillRegistryError(f"GET {ref} returned {resp.status_code}")
        text = resp.text
    else:
        p = Path(ref)
        if not p.exists():
            raise SkillRegistryError(f"{ref}: no such file")
        text = p.read_text()
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SkillRegistryError(f"{ref}: not YAML — {exc}") from exc
    if not isinstance(raw, dict):
        raise SkillRegistryError(f"{ref}: expected a Skill or SkillBundle document")
    return raw


def plan_add(workspace: Path, ref: str, *, catalogue: Catalogue | None = None) -> AddPlan:
    """Resolve *ref* — a catalogue skill id, a catalogue bundle id, or a path/URL to a Skill or
    SkillBundle file — into the files and the server entry adding it needs."""
    skills_dir = workspace / "skills"
    ws_doc = _workspace_doc(workspace)
    existing_servers = {
        str(s.get("id")): s for s in (ws_doc.get("mcp_servers") or []) if isinstance(s, dict)
    }
    notes: list[str] = []

    if _is_url(ref) or ref.endswith((".yaml", ".yml")) or "/" in ref:
        raw = _load_file_or_url(ref)
        kind = str(raw.get("kind") or "")
        if kind == "SkillBundle":
            return _plan_bundle_from_raw(raw, ref, skills_dir, existing_servers, notes)
        if kind != "Skill":
            raise SkillRegistryError(
                f"{ref}: kind is {kind or 'missing'}, expected Skill or SkillBundle"
            )
        sid = str((raw.get("metadata") or {}).get("id") or "")
        if not sid:
            raise SkillRegistryError(f"{ref}: the skill has no metadata.id")
        return AddPlan(
            skill_files={skills_dir / f"{sid}.yaml": _dump(raw)},
            server_entry=None,
            server_state="new",
            notes=tuple(notes),
        )

    cat = catalogue or load_catalogue()
    if ref in cat.bundles:
        bundle = cat.bundles[ref]
        ok, have = _floor_ok(bundle.requires_runtime)
        if not ok:
            raise SkillRegistryError(
                f"bundle '{ref}' needs runtime {bundle.requires_runtime}; this is {have}"
            )
        files = {
            skills_dir / f"{sid}.yaml": _dump(cat.skills[sid].raw)
            for sid in bundle.skill_ids
            if sid in cat.skills
        }
        server = bundle.server
        if bundle.verification:
            notes.append(_verification_note(bundle))
        return AddPlan(
            skill_files=files,
            server_entry=server,
            server_state=_server_state(server, existing_servers),
            notes=tuple(notes),
        )
    if ref in cat.skills:
        skill = cat.skills[ref]
        owner = cat.bundles.get(skill.bundle)
        floor = (skill.raw.get("provenance") or {}).get("requires_runtime") or (
            owner.requires_runtime if owner else None
        )
        ok, have = _floor_ok(floor)
        if not ok:
            raise SkillRegistryError(f"skill '{ref}' needs runtime {floor}; this is {have}")
        server = None
        if skill.server_id and owner and owner.server:
            server = owner.server
        if owner and owner.verification:
            notes.append(_verification_note(owner))
        return AddPlan(
            skill_files={skills_dir / f"{ref}.yaml": _dump(skill.raw)},
            server_entry=server,
            server_state=_server_state(server, existing_servers),
            notes=tuple(notes),
        )
    close = cat.suggest(ref)
    hint = f" — did you mean {', '.join(close)}?" if close else ""
    raise SkillRegistryError(
        f"'{ref}' is not in the catalogue ({cat.source}){hint}; "
        "`swarmkit skill search <words>` lists what is"
    )


def _plan_bundle_from_raw(
    raw: dict[str, Any],
    ref: str,
    skills_dir: Path,
    existing_servers: dict[str, dict[str, Any]],
    notes: list[str],
) -> AddPlan:
    """A SkillBundle file given directly carries only ids; its skills must sit beside it as
    ``skills/<id>.yaml`` (a catalogue checkout) — otherwise say so rather than write nothing."""
    if _is_url(ref):
        raise SkillRegistryError(
            "a SkillBundle by URL cannot fetch its skills; add the bundle by catalogue id instead"
        )
    base = Path(ref).parent
    files: dict[Path, str] = {}
    for sid in raw.get("skills") or []:
        p = base / "skills" / f"{sid}.yaml"
        if not p.exists():
            raise SkillRegistryError(f"{ref}: skill '{sid}' not found at {p}")
        files[skills_dir / f"{sid}.yaml"] = _dump(yaml.safe_load(p.read_text()) or {})
    server = raw.get("server") if isinstance(raw.get("server"), dict) else None
    return AddPlan(
        skill_files=files,
        server_entry=server,
        server_state=_server_state(server, existing_servers),
        notes=tuple(notes),
    )


def _server_state(server: dict[str, Any] | None, existing: dict[str, dict[str, Any]]) -> str:
    if server is None:
        return "new"
    have = existing.get(str(server.get("id")))
    if have is None:
        return "new"
    return "same" if _normalised(have) == _normalised(server) else "differs"


def _normalised(entry: dict[str, Any]) -> Any:
    return json.loads(json.dumps(entry, sort_keys=True, default=str))


def _verification_note(bundle: CatalogueBundle) -> str:
    v = bundle.verification
    state = str(v.get("state") or "unknown")
    when = str(v.get("checked_at") or "")
    return f"catalogue verification: {state}" + (f" (checked {when})" if when else "")


def _workspace_doc(workspace: Path) -> dict[str, Any]:
    p = workspace / "workspace.yaml"
    if not p.exists():
        raise SkillRegistryError(f"{workspace} has no workspace.yaml")
    raw = yaml.safe_load(p.read_text()) or {}
    return raw if isinstance(raw, dict) else {}


def _dump(raw: dict[str, Any]) -> str:
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)


def apply_add(workspace: Path, plan: AddPlan) -> dict[str, Any]:
    """Write the plan: skill files, then the ``mcp_servers`` upsert through the workspace config
    service (comments intact; an entry that would not load is rolled back). Validates the
    workspace afterwards and reports what was written."""
    from swarmkit_runtime.server._workspace_config import (  # noqa: PLC0415
        ConfigError,
        WorkspaceConfigService,
    )

    written: list[str] = []
    for path, text in plan.skill_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text() == text:
            continue  # idempotent
        path.write_text(text)
        written.append(str(path.relative_to(workspace)))
    server_written = False
    if plan.server_entry is not None and plan.server_state != "same":
        entry = dict(plan.server_entry)
        sid = str(entry.pop("id"))
        try:
            result = WorkspaceConfigService(workspace).upsert("mcp_servers", sid, entry)
        except ConfigError as exc:
            raise SkillRegistryError(f"mcp_servers/{sid}: {exc}") from exc
        if not result.get("saved", True):
            # The config service rolled workspace.yaml back; the skill files stay — they are
            # inert without the server and the message says what to fix.
            errors = "; ".join(str(e.get("message", e)) for e in result.get("errors", []))
            raise SkillRegistryError(f"mcp_servers/{sid}: workspace would not load — {errors}")
        server_written = True
    return {"skill_files": written, "server": server_written}


# ---- import: SKILL.md → llm_prompt skill --------------------------------------------------------

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)


def convert_skill_md(text: str, origin: str) -> dict[str, Any]:
    """An Agent Skills ``SKILL.md`` (YAML frontmatter over a markdown body) as a SwarmKit
    ``llm_prompt`` skill. The body is the prompt, verbatim; a SKILL.md that leans on scripts or
    bundled files keeps those references in the prompt for a person to see."""
    m = _FRONTMATTER.match(text)
    if not m:
        raise SkillRegistryError(f"{origin}: no YAML frontmatter (expected --- name: … ---)")
    try:
        front = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillRegistryError(f"{origin}: frontmatter is not YAML — {exc}") from exc
    body = m.group(2).strip()
    name = str(front.get("name") or "").strip()
    if not name:
        raise SkillRegistryError(f"{origin}: frontmatter has no `name`")
    sid = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    if not re.match(r"^[a-z][a-z0-9-]*$", sid):
        raise SkillRegistryError(f"{origin}: cannot make a skill id from name {name!r}")
    if not body:
        raise SkillRegistryError(f"{origin}: the body (the instructions) is empty")
    meta = front.get("metadata") or {}
    version = str(meta.get("version") or front.get("version") or "1.0.0")
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        version = version + ".0" * (2 - version.count(".")) if version.count(".") < 2 else "1.0.0"
    raw: dict[str, Any] = {
        "apiVersion": "swarmkit/v1",
        "kind": "Skill",
        "metadata": {
            "id": sid,
            "name": name.replace("-", " ").title() if "-" in name else name,
            "description": str(front.get("description") or f"Imported from {origin}"),
        },
        "category": "capability",
        "implementation": {"type": "llm_prompt", "prompt": body + "\n"},
        "provenance": {
            "authored_by": "imported_from_registry",
            "version": version,
            "registry": origin,
        },
    }
    from swarmkit_schema import validate  # noqa: PLC0415

    try:
        validate("skill", raw)
    except Exception as exc:  # the validator's own message names the field
        raise SkillRegistryError(f"{origin}: converted skill is not valid — {exc}") from exc
    return raw


def import_skill_md(workspace: Path, ref: str) -> tuple[Path, dict[str, Any]]:
    """Convert and write ``skills/<id>.yaml``; returns the path and the skill."""
    if _is_url(ref):
        resp = httpx.get(ref, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            raise SkillRegistryError(f"GET {ref} returned {resp.status_code}")
        text = resp.text
    else:
        p = Path(ref)
        if p.is_dir():
            p = p / "SKILL.md"
        if not p.exists():
            raise SkillRegistryError(f"{ref}: no such file")
        text = p.read_text()
    raw = convert_skill_md(text, ref)
    out = workspace / "skills" / f"{raw['metadata']['id']}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_dump(raw))
    return out, raw


# ---- check: are the tools still there? ----------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    skill_id: str
    server_id: str
    tool: str
    status: str  # ok | missing | server-failed
    detail: str = ""


async def check_skills(workspace: Path) -> list[CheckResult]:
    """For every ``mcp_tool`` skill, start its server the way a run would and ask whether the
    tool it names is still there. Reports; changes nothing."""
    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415

    rt = WorkspaceRuntime.from_workspace_path(workspace)
    manager = getattr(rt, "mcp_manager", None) or getattr(rt, "_mcp_manager", None)
    results: list[CheckResult] = []
    tools_by_server: dict[str, list[str] | Exception] = {}
    for sid, skill in sorted(rt.workspace.skills.items()):
        impl = skill.raw.implementation
        if _impl_get(impl, "type") != "mcp_tool":
            continue
        server_id = str(_impl_get(impl, "server") or "")
        tool = str(_impl_get(impl, "tool") or "")
        if server_id not in tools_by_server:
            try:
                if manager is None:
                    raise RuntimeError("no MCP manager on this runtime")
                listed = await manager.list_tools(server_id)
                tools_by_server[server_id] = [str(t.get("name")) for t in listed]
            except Exception as exc:  # every failure mode is a report line
                tools_by_server[server_id] = exc
        got = tools_by_server[server_id]
        if isinstance(got, Exception):
            results.append(CheckResult(sid, server_id, tool, "server-failed", str(got)[:200]))
        elif tool in got:
            results.append(CheckResult(sid, server_id, tool, "ok"))
        else:
            results.append(
                CheckResult(sid, server_id, tool, "missing", f"server has: {', '.join(got)}")
            )
    close = getattr(manager, "close_all", None) or getattr(manager, "aclose", None)
    if close is not None:
        try:
            await close()
        except Exception:
            logger.debug("check: closing MCP sessions failed", exc_info=True)
    return results


# ---- remove ---------------------------------------------------------------------------------


def remove_skill(workspace: Path, skill_id: str) -> Path:
    """Delete ``skills/<id>.yaml``; refused while an archetype or agent grants it."""
    skills = {s.id: s for s in workspace_skills(workspace)}
    s = skills.get(skill_id)
    if s is None:
        raise SkillRegistryError(f"'{skill_id}' is not a skill file in this workspace")
    if s.held_by:
        raise SkillRegistryError(
            f"'{skill_id}' is held by {', '.join(s.held_by)} — remove the grant first"
        )
    s.path.unlink()
    return s.path


def origin_host(ref: str) -> str:
    return urlparse(ref).netloc if _is_url(ref) else ref
