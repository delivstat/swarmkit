"""Archetype registry + skill-reference verification (M1.4 / phase 3b).

Given a list of archetype ``DiscoveredArtifact`` and the already-built
skill registry (from :mod:`swael_runtime.skills`), produce a registry
of :class:`ResolvedArchetype` keyed by archetype ID.

Concrete skill references in an archetype's ``defaults.skills`` list are
verified against the skill registry. Abstract-skill placeholders
(``{ abstract: { category, capability? } }`` per §6.6) are left
unchecked here — they bind to a concrete skill at topology-load time in
phase 3c (M1.5).

Design reference: ``design/details/topology-loader.md`` phase 3b.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError
from swael_schema.models import SwarmKitArchetype

from swael_runtime.errors import ResolutionError
from swael_runtime.skills import ResolvedSkill
from swael_runtime.workspace import DiscoveredArtifact


@dataclass(frozen=True)
class ResolvedArchetype:
    """An archetype whose concrete skill references have been verified
    against the workspace skill registry.

    Abstract-skill placeholders in ``raw.defaults.skills`` are retained
    as-is — the topology resolver (M1.5) binds them per-agent.
    """

    id: str
    raw: SwarmKitArchetype
    source_path: Path


def build_archetype_registry(
    artifacts: Iterable[DiscoveredArtifact],
    skill_registry: Mapping[str, ResolvedSkill],
) -> tuple[Mapping[str, ResolvedArchetype], list[ResolutionError]]:
    """Build a registry of :class:`ResolvedArchetype` keyed by ID.

    Returns ``(registry, errors)``. Every concrete skill reference in an
    archetype's defaults must exist in ``skill_registry``; unknown refs
    surface as ``archetype.unknown-skill`` entries. Duplicate archetype
    IDs surface as ``archetype.duplicate-id``.
    """
    errors: list[ResolutionError] = []
    registry: dict[str, ResolvedArchetype] = {}

    for artifact in artifacts:
        if artifact.kind != "archetype":
            continue
        try:
            model = SwarmKitArchetype.model_validate(dict(artifact.raw))
        except PydanticValidationError as exc:
            errors.append(
                ResolutionError(
                    code="archetype.model-construction",
                    message=(
                        f"archetype at {artifact.path} could not be "
                        "constructed as a pydantic SwarmKitArchetype model."
                    ),
                    artifact_path=artifact.path,
                    suggestion=f"pydantic raised: {exc.errors()[0]['msg']}",
                )
            )
            continue
        arch_id = _identifier_str(model.metadata.id)
        if arch_id in registry:
            prior_path = registry[arch_id].source_path
            errors.append(
                ResolutionError(
                    code="archetype.duplicate-id",
                    message=(
                        f"Archetype id {arch_id!r} is declared twice: "
                        f"first at {prior_path}, again at {artifact.path}."
                    ),
                    artifact_path=artifact.path,
                    yaml_pointer="/metadata/id",
                    suggestion=(
                        "Rename one of the archetypes so every archetype ID "
                        "is unique within the workspace."
                    ),
                )
            )
            continue

        # Verify every concrete skill reference exists. Abstract
        # placeholders skipped here; their binding happens in phase 3c.
        skill_errors = _check_skill_refs(arch_id, artifact.path, model, skill_registry)
        errors.extend(skill_errors)
        # Even with skill errors, keep the archetype in the registry so
        # later phases can produce more useful cross-references.
        registry[arch_id] = ResolvedArchetype(id=arch_id, raw=model, source_path=artifact.path)

    return registry, errors


def _check_skill_refs(
    arch_id: str,
    source: Path,
    model: SwarmKitArchetype,
    skill_registry: Mapping[str, ResolvedSkill],
) -> list[ResolutionError]:
    errors: list[ResolutionError] = []
    defaults = model.defaults
    skills = getattr(defaults, "skills", None)
    if not skills:
        return errors
    for index, entry in enumerate(skills):
        # Entry is either a bare identifier string or an object with
        # ``abstract: { category, capability? }``. Only the concrete
        # case is checked here.
        if _is_abstract_skill(entry):
            continue
        skill_id = _identifier_str(entry)
        if skill_id not in skill_registry:
            errors.append(
                ResolutionError(
                    code="archetype.unknown-skill",
                    message=(
                        f"Archetype {arch_id!r} references skill "
                        f"{skill_id!r} which is not defined in this workspace."
                    ),
                    artifact_path=source,
                    yaml_pointer=f"/defaults/skills/{index}",
                    suggestion=(
                        f"Define a skill with id={skill_id!r}, or change "
                        "the reference to an existing one. You can also "
                        "use an abstract placeholder "
                        "({ abstract: { category, capability? } }) if the "
                        "archetype should be concrete-skill-agnostic."
                    ),
                )
            )
    return errors


def _is_abstract_skill(entry: object) -> bool:
    # The skill entry union is string | { abstract: {...} }. pydantic
    # materialises the string branch as a plain string and the object
    # branch as an Abstract-containing object. Duck-type check:
    # anything with an `abstract` attribute that isn't None is abstract.
    return getattr(entry, "abstract", None) is not None


def _identifier_str(value: object) -> str:
    root = getattr(value, "root", value)
    return str(root)


__all__ = [
    "ResolvedArchetype",
    "build_archetype_registry",
]
