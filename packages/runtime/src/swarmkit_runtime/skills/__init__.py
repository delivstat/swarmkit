"""Skill registry + composed-skill resolution (M1.4 / phase 3a).

Given a list of skill ``DiscoveredArtifact``, build a registry keyed by
skill ID. Detect composed-skill cycles. Verify that every ID a composed
skill references exists in the registry.

Abstract-skill placeholders (§6.6 edge case) are **archetype-level**
concepts and therefore are not seen here — the archetype schema's
``abstract`` objects never appear as skill artifacts.

Design reference: ``design/details/topology-loader.md`` phase 3a.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from swarmkit_schema.models import SwarmKitSkill

from swarmkit_runtime.errors import ResolutionError
from swarmkit_runtime.workspace import DiscoveredArtifact


def impl_get(impl: Any, key: str, default: Any = "") -> Any:
    """Access a skill implementation field regardless of whether it's a dict or pydantic model."""
    if isinstance(impl, dict):
        return impl.get(key, default)
    return getattr(impl, key, default)


@dataclass(frozen=True)
class ResolvedSkill:
    """A skill that has been validated, typed, and cross-referenced against
    the skill registry. For composed skills, ``resolved_composes`` holds the
    concrete :class:`ResolvedSkill` references; for leaf skills it is
    empty.
    """

    id: str
    raw: SwarmKitSkill
    source_path: Path
    resolved_composes: tuple[ResolvedSkill, ...] = field(default_factory=tuple)


def build_skill_registry(
    artifacts: Iterable[DiscoveredArtifact],
) -> tuple[Mapping[str, ResolvedSkill], list[ResolutionError]]:
    """Build a registry of ResolvedSkill keyed by ID.

    Returns ``(registry, errors)``. If ``errors`` is non-empty the
    registry still contains everything that could be resolved;
    downstream code should check ``errors`` before trusting the
    registry. (The M1.5 orchestrator raises :class:`ResolutionErrors`
    on non-empty errors; this function keeps its contract smaller.)
    """
    errors: list[ResolutionError] = []
    # Pass 1: build ID → (artifact, model) map. Duplicates emit one error
    # per offender and keep the first occurrence in the map (deterministic
    # — artifacts arrive in sorted order from discover()).
    by_id: dict[str, tuple[DiscoveredArtifact, SwarmKitSkill]] = {}
    for artifact in artifacts:
        if artifact.kind != "skill":
            continue
        try:
            model = SwarmKitSkill.model_validate(dict(artifact.raw))
        except PydanticValidationError as exc:
            errors.append(
                ResolutionError(
                    code="skill.model-construction",
                    message=(
                        f"skill at {artifact.path} could not be constructed "
                        f"as a pydantic SwarmKitSkill model. This normally "
                        f"indicates the JSON Schema layer missed something; "
                        f"upstream validation should have caught this."
                    ),
                    artifact_path=artifact.path,
                    suggestion=f"pydantic raised: {exc.errors()[0]['msg']}",
                )
            )
            continue
        skill_id = _identifier_str(model.metadata.id)
        if skill_id in by_id:
            prior_path = by_id[skill_id][0].path
            errors.append(
                ResolutionError(
                    code="skill.duplicate-id",
                    message=(
                        f"Skill id {skill_id!r} is declared twice: "
                        f"first at {prior_path}, again at {artifact.path}."
                    ),
                    artifact_path=artifact.path,
                    yaml_pointer="/metadata/id",
                    suggestion=(
                        "Rename one of the skills so every skill ID is unique within the workspace."
                    ),
                )
            )
            continue
        by_id[skill_id] = (artifact, model)

    # Pass 2: construct ResolvedSkill for leaf skills first, then composed.
    # Composed resolution needs the leaves in place.
    registry: dict[str, ResolvedSkill] = {}
    for skill_id, (artifact, model) in by_id.items():
        if not _is_composed(model):
            registry[skill_id] = ResolvedSkill(
                id=skill_id,
                raw=model,
                source_path=artifact.path,
            )

    # Pass 3: composed skills. Also detects unknown references and cycles.
    for skill_id, (artifact, model) in by_id.items():
        if _is_composed(model):
            resolved, sub_errors = _resolve_composed(
                skill_id, artifact, model, by_id, registry, set()
            )
            if sub_errors:
                errors.extend(sub_errors)
                continue
            if resolved is not None:
                registry[skill_id] = resolved

    return registry, errors


def _is_composed(model: SwarmKitSkill) -> bool:
    return getattr(model.implementation, "type", None) == "composed"


def _resolve_composed(
    skill_id: str,
    artifact: DiscoveredArtifact,
    model: SwarmKitSkill,
    by_id: Mapping[str, tuple[DiscoveredArtifact, SwarmKitSkill]],
    registry: dict[str, ResolvedSkill],
    in_progress: set[str],
) -> tuple[ResolvedSkill | None, list[ResolutionError]]:
    """Resolve a composed skill's ``composes`` list.

    ``in_progress`` tracks the current DFS path for cycle detection.
    ``registry`` is mutated as composed-of-composed chains resolve.
    """
    if skill_id in registry:
        return registry[skill_id], []
    if skill_id in in_progress:
        cycle_path = [*in_progress, skill_id]
        return None, [
            ResolutionError(
                code="skill.composed-cycle",
                message=(
                    f"Composed skill {skill_id!r} participates in a cycle: {' → '.join(cycle_path)}"
                ),
                artifact_path=artifact.path,
                yaml_pointer="/implementation/composes",
                suggestion=(
                    "Break the cycle — a composed skill cannot (transitively) reference itself."
                ),
            )
        ]

    in_progress.add(skill_id)
    errors: list[ResolutionError] = []
    resolved_composes: list[ResolvedSkill] = []
    composes = _composes_list(model)

    for index, referenced_id in enumerate(composes):
        if referenced_id in registry:
            resolved_composes.append(registry[referenced_id])
            continue
        referenced = by_id.get(referenced_id)
        if referenced is None:
            errors.append(
                ResolutionError(
                    code="skill.composed-unknown",
                    message=(
                        f"Composed skill {skill_id!r} references unknown skill {referenced_id!r}."
                    ),
                    artifact_path=artifact.path,
                    yaml_pointer=f"/implementation/composes/{index}",
                    suggestion=(
                        f"Define a skill with id={referenced_id!r}, or remove "
                        "the reference from composes."
                    ),
                )
            )
            continue
        # Referenced skill exists but isn't resolved yet — recurse.
        ref_artifact, ref_model = referenced
        if _is_composed(ref_model):
            nested, nested_errors = _resolve_composed(
                referenced_id,
                ref_artifact,
                ref_model,
                by_id,
                registry,
                in_progress,
            )
            errors.extend(nested_errors)
            if nested is not None:
                resolved_composes.append(nested)
        else:
            # Leaf skill not in registry — shouldn't happen given pass 2
            # populates leaves first, but be defensive.
            leaf = ResolvedSkill(
                id=referenced_id,
                raw=ref_model,
                source_path=ref_artifact.path,
            )
            registry[referenced_id] = leaf
            resolved_composes.append(leaf)

    in_progress.discard(skill_id)

    if errors:
        return None, errors

    resolved = ResolvedSkill(
        id=skill_id,
        raw=model,
        source_path=artifact.path,
        resolved_composes=tuple(resolved_composes),
    )
    registry[skill_id] = resolved
    return resolved, []


def _composes_list(model: SwarmKitSkill) -> list[str]:
    impl = model.implementation
    composes = getattr(impl, "composes", None)
    if composes is None:
        return []
    # The generated pydantic model wraps list-of-identifier with its own
    # type; normalise to a list of plain strings.
    return [_identifier_str(x) for x in composes]


def _identifier_str(value: object) -> str:
    root = getattr(value, "root", value)
    return str(root)


__all__ = [
    "ResolvedSkill",
    "build_skill_registry",
]
