"""Phase 3c — topology resolution.

Walks each topology's agent tree, merges archetype defaults into every
agent (precedence rules in ``design/details/topology-loader.md``),
resolves every skill reference (concrete IDs + abstract placeholders),
and returns a :class:`ResolvedTopology` per topology plus the list of
any resolution errors that surfaced.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from swael_schema.models import SwarmKitTopology

from swael_runtime.archetypes import ResolvedArchetype
from swael_runtime.errors import ResolutionError, yaml_pointer
from swael_runtime.skills import ResolvedSkill
from swael_runtime.workspace import DiscoveredArtifact

from ._resolved import ResolvedAgent, ResolvedTopology


def build_topology_registry(
    artifacts: Iterable[DiscoveredArtifact],
    skills: Mapping[str, ResolvedSkill],
    archetypes: Mapping[str, ResolvedArchetype],
) -> tuple[Mapping[str, ResolvedTopology], list[ResolutionError]]:
    errors: list[ResolutionError] = []
    registry: dict[str, ResolvedTopology] = {}

    for artifact in artifacts:
        if artifact.kind != "topology":
            continue
        resolved, sub_errors = _resolve_topology(artifact, skills, archetypes)
        errors.extend(sub_errors)
        if resolved is None:
            continue
        if resolved.id in registry:
            prior = registry[resolved.id].source_path
            errors.append(
                ResolutionError(
                    code="topology.duplicate-id",
                    message=(
                        f"Topology id {resolved.id!r} is declared twice: "
                        f"first at {prior}, again at {artifact.path}."
                    ),
                    artifact_path=artifact.path,
                    yaml_pointer="/metadata/name",
                    suggestion=(
                        "Rename one of the topologies so every topology ID "
                        "is unique within the workspace."
                    ),
                )
            )
            continue
        registry[resolved.id] = resolved

    return registry, errors


def _resolve_topology(
    artifact: DiscoveredArtifact,
    skills: Mapping[str, ResolvedSkill],
    archetypes: Mapping[str, ResolvedArchetype],
) -> tuple[ResolvedTopology | None, list[ResolutionError]]:
    errors: list[ResolutionError] = []
    try:
        model = SwarmKitTopology.model_validate(dict(artifact.raw))
    except PydanticValidationError as exc:
        errors.append(
            ResolutionError(
                code="topology.model-construction",
                message=(
                    f"topology at {artifact.path} could not be constructed "
                    "as a pydantic SwarmKitTopology model."
                ),
                artifact_path=artifact.path,
                suggestion=f"pydantic raised: {exc.errors()[0]['msg']}",
            )
        )
        return None, errors

    raw = dict(artifact.raw)
    agents_block = raw.get("agents", {})
    root_raw = agents_block.get("root", {})
    seen_ids: set[str] = set()
    root, sub_errors = _resolve_agent(
        raw_agent=root_raw,
        parent_path=[],
        skills=skills,
        archetypes=archetypes,
        seen_ids=seen_ids,
        artifact_path=artifact.path,
    )
    errors.extend(sub_errors)
    if root is None:
        return None, errors

    topology_id = _topology_id_from_raw(raw)
    return (
        ResolvedTopology(
            id=topology_id,
            raw=model,
            source_path=artifact.path,
            root=root,
        ),
        errors,
    )


def _topology_id_from_raw(raw: Mapping[str, Any]) -> str:
    # Topology ID = metadata.name (kebab-identifier, per the schema).
    metadata = raw.get("metadata", {})
    name = metadata.get("name")
    return str(name) if name is not None else "<unnamed>"


def _resolve_agent(
    raw_agent: Mapping[str, Any],
    parent_path: Sequence[str | int],
    skills: Mapping[str, ResolvedSkill],
    archetypes: Mapping[str, ResolvedArchetype],
    seen_ids: set[str],
    artifact_path: Path,
) -> tuple[ResolvedAgent | None, list[ResolutionError]]:
    errors: list[ResolutionError] = []
    agent_id = str(raw_agent.get("id", ""))
    role = raw_agent.get("role", "")

    if agent_id in seen_ids:
        errors.append(
            ResolutionError(
                code="agent.duplicate-id",
                message=(
                    f"Agent id {agent_id!r} appears more than once in this "
                    "topology; every agent id must be unique."
                ),
                artifact_path=artifact_path,
                yaml_pointer=_pointer_with(parent_path, "id"),
                suggestion=("Rename one of the agents so every id is unique within the topology."),
            )
        )
    seen_ids.add(agent_id)

    if role not in ("root", "leader", "worker"):
        errors.append(
            ResolutionError(
                code="agent.bad-role",
                message=(
                    f"Agent {agent_id!r} has role {role!r}; expected one of root | leader | worker."
                ),
                artifact_path=artifact_path,
                yaml_pointer=_pointer_with(parent_path, "role"),
            )
        )

    archetype_id = raw_agent.get("archetype")
    archetype: ResolvedArchetype | None = None
    if archetype_id is not None:
        archetype = archetypes.get(str(archetype_id))
        if archetype is None:
            errors.append(
                ResolutionError(
                    code="agent.unknown-archetype",
                    message=(
                        f"Agent {agent_id!r} references archetype "
                        f"{archetype_id!r} which is not defined in this "
                        "workspace."
                    ),
                    artifact_path=artifact_path,
                    yaml_pointer=_pointer_with(parent_path, "archetype"),
                    suggestion=(
                        f"Define an archetype with id={archetype_id!r}, or "
                        "remove the archetype reference and declare model, "
                        "prompt, and skills directly on the agent."
                    ),
                )
            )

    model = _merge_model(archetype, raw_agent)
    prompt = _merge_prompt(archetype, raw_agent)
    iam = _merge_iam(archetype, raw_agent)
    skills_resolved, skill_errors = _merge_and_resolve_skills(
        archetype,
        raw_agent,
        skills,
        agent_id,
        parent_path,
        artifact_path,
    )
    errors.extend(skill_errors)

    # Children.
    children_raw = raw_agent.get("children") or []
    resolved_children: list[ResolvedAgent] = []
    for index, child_raw in enumerate(children_raw):
        child, child_errors = _resolve_agent(
            raw_agent=child_raw,
            parent_path=[*parent_path, "children", index],
            skills=skills,
            archetypes=archetypes,
            seen_ids=seen_ids,
            artifact_path=artifact_path,
        )
        errors.extend(child_errors)
        if child is not None:
            resolved_children.append(child)

    if role not in ("root", "leader", "worker"):
        # Drop the bad agent; don't materialise with an invalid literal.
        return None, errors

    return (
        ResolvedAgent(
            id=agent_id,
            role=role,
            model=model,
            prompt=prompt,
            skills=skills_resolved,
            iam=iam,
            children=tuple(resolved_children),
            source_archetype=str(archetype_id) if archetype_id else None,
        ),
        errors,
    )


# ---- merge helpers -----------------------------------------------------


def _merge_model(
    archetype: ResolvedArchetype | None, raw_agent: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    return _shallow_merge(
        _archetype_default(archetype, "model"),
        raw_agent.get("model"),
    )


def _merge_prompt(
    archetype: ResolvedArchetype | None, raw_agent: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    return _shallow_merge(
        _archetype_default(archetype, "prompt"),
        raw_agent.get("prompt"),
    )


def _merge_iam(
    archetype: ResolvedArchetype | None, raw_agent: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    return _shallow_merge(
        _archetype_default(archetype, "iam"),
        raw_agent.get("iam"),
    )


def _archetype_default(
    archetype: ResolvedArchetype | None, field_name: str
) -> Mapping[str, Any] | None:
    if archetype is None:
        return None
    defaults_model = archetype.raw.defaults
    value = getattr(defaults_model, field_name, None)
    if value is None:
        return None
    return _to_dict(value)


def _shallow_merge(
    base: Mapping[str, Any] | None, override: Mapping[str, Any] | None
) -> Mapping[str, Any] | None:
    """Agent-level override keys win over archetype-level defaults.

    Neither present -> None. Base only -> base. Override only -> override.
    Both present -> shallow merge with override winning per-key.
    """
    if base is None and override is None:
        return None
    if base is None:
        return dict(override) if override is not None else None
    if override is None:
        return dict(base)
    return {**base, **override}


# ---- skill merge + resolution ------------------------------------------


def _merge_and_resolve_skills(
    archetype: ResolvedArchetype | None,
    raw_agent: Mapping[str, Any],
    skills: Mapping[str, ResolvedSkill],
    agent_id: str,
    parent_path: Sequence[str | int],
    artifact_path: Path,
) -> tuple[tuple[ResolvedSkill, ...], list[ResolutionError]]:
    """Merge archetype and agent skill lists per §6.6, then resolve each
    entry (concrete ID or abstract placeholder) into a ResolvedSkill.
    """
    # Precedence:
    # (a) archetype defaults.skills (list of entries — concrete strings or abstract objs)
    # (b) agent-level `skills`  -> replaces (a)
    # (c) agent-level `skills_additional` -> appended to the resulting list
    archetype_entries = _archetype_skill_entries(archetype)
    agent_skills = raw_agent.get("skills")
    agent_additional = raw_agent.get("skills_additional")

    if agent_skills is not None:
        entries: list[Any] = list(agent_skills)
    else:
        entries = list(archetype_entries)

    if agent_additional:
        entries.extend(list(agent_additional))

    resolved: list[ResolvedSkill] = []
    errors: list[ResolutionError] = []
    for index, entry in enumerate(entries):
        skill_or_error = _resolve_skill_entry(
            entry, skills, agent_id, parent_path, index, artifact_path
        )
        if isinstance(skill_or_error, ResolutionError):
            errors.append(skill_or_error)
        else:
            resolved.append(skill_or_error)
    return tuple(resolved), errors


def _archetype_skill_entries(archetype: ResolvedArchetype | None) -> list[Any]:
    """Return the archetype's defaults.skills list as raw entries (strings
    or abstract-placeholder dicts)."""
    if archetype is None:
        return []
    defaults = archetype.raw.defaults
    raw_skills = getattr(defaults, "skills", None)
    if not raw_skills:
        return []
    # The generated model's skill entries may be strings or objects
    # with an ``abstract`` attribute. Normalise to raw dicts / strings.
    normalised: list[Any] = []
    for entry in raw_skills:
        if isinstance(entry, str):
            normalised.append(entry)
            continue
        abstract = getattr(entry, "abstract", None)
        if abstract is not None:
            normalised.append(
                {
                    "abstract": {
                        "category": _to_str_or_none(getattr(abstract, "category", None)),
                        "capability": getattr(abstract, "capability", None),
                    }
                }
            )
            continue
        # RootModel-wrapped identifier (Identifier(root='...')).
        root = getattr(entry, "root", None)
        if isinstance(root, str):
            normalised.append(root)
            continue
        # Fallback — treat as opaque.
        normalised.append(_to_dict(entry))
    return normalised


def _resolve_skill_entry(
    entry: Any,
    skills: Mapping[str, ResolvedSkill],
    agent_id: str,
    parent_path: Sequence[str | int],
    index: int,
    artifact_path: Path,
) -> ResolvedSkill | ResolutionError:
    """Resolve one skill-list entry. Returns either a ResolvedSkill or a
    structured ResolutionError (caller aggregates).
    """
    # Concrete skill ID.
    if isinstance(entry, str):
        skill = skills.get(entry)
        if skill is None:
            return ResolutionError(
                code="agent.unknown-skill",
                message=(
                    f"Agent {agent_id!r} references skill {entry!r} which is "
                    "not defined in this workspace."
                ),
                artifact_path=artifact_path,
                yaml_pointer=_pointer_with(parent_path, "skills", index),
                suggestion=(
                    f"Define a skill with id={entry!r} under skills/, or "
                    "change the reference to an existing one."
                ),
            )
        return skill

    # Abstract placeholder: {"abstract": {"category": ..., "capability": ...?}}
    abstract = None
    if isinstance(entry, dict):
        abstract = entry.get("abstract")
    if not isinstance(abstract, dict):
        return ResolutionError(
            code="agent.bad-skill-entry",
            message=(
                f"Agent {agent_id!r} has a skill entry that is neither a "
                "concrete skill id (string) nor an abstract placeholder."
            ),
            artifact_path=artifact_path,
            yaml_pointer=_pointer_with(parent_path, "skills", index),
        )

    category = abstract.get("category")
    capability = abstract.get("capability")
    matches = _match_abstract(skills, category, capability)
    if len(matches) == 1:
        return matches[0]
    if len(matches) == 0:
        return ResolutionError(
            code="agent.abstract-no-match",
            message=(
                f"Agent {agent_id!r} has an abstract skill placeholder "
                f"{{category={category!r}, capability={capability!r}}} but no "
                "skill in the workspace matches."
            ),
            artifact_path=artifact_path,
            yaml_pointer=_pointer_with(parent_path, "skills", index),
            suggestion=(
                "Define a skill whose category matches and whose id includes "
                f"{capability!r}, or reference a skill concretely."
            ),
        )
    candidates = ", ".join(sorted(s.id for s in matches))
    return ResolutionError(
        code="agent.abstract-ambiguous",
        message=(
            f"Agent {agent_id!r} has an abstract skill placeholder "
            f"{{category={category!r}, capability={capability!r}}} that "
            f"matches multiple skills: {candidates}."
        ),
        artifact_path=artifact_path,
        yaml_pointer=_pointer_with(parent_path, "skills", index),
        suggestion=(
            "Rename the skills to disambiguate, or reference the intended skill concretely."
        ),
    )


def _match_abstract(
    skills: Mapping[str, ResolvedSkill],
    category: Any,
    capability: Any,
) -> list[ResolvedSkill]:
    cat_str = _to_str_or_none(category)
    cap_str = capability if isinstance(capability, str) else None
    matches: list[ResolvedSkill] = []
    for skill in skills.values():
        skill_cat = _to_str_or_none(skill.raw.category)
        if cat_str is not None and skill_cat != cat_str:
            continue
        if cap_str is not None and cap_str not in skill.id:
            continue
        matches.append(skill)
    return matches


# ---- small helpers -----------------------------------------------------


def _to_dict(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump(mode="json", exclude_none=True, by_alias=True)
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _to_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    root = getattr(value, "value", None)
    if isinstance(root, str):
        return root
    return str(value)


def _pointer_with(parts: Sequence[str | int], *extra: str | int) -> str:
    """Build a yaml_pointer string prefixed with ``/agents/root`` plus any
    additional segments. Agents are always rooted at ``agents.root`` in the
    topology schema.
    """
    full: list[object] = ["agents", "root", *parts, *extra]
    return yaml_pointer(full)


__all__ = [
    "build_topology_registry",
]
