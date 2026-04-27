"""Trigger resolution — verifies every trigger's target topology exists.

Triggers don't feed into the agent tree; they're surfaced as a separate
``Sequence[ResolvedTrigger]`` on :class:`ResolvedWorkspace`. The runtime
(M9 HTTP server / scheduler) consumes the trigger list.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import ValidationError as PydanticValidationError
from swael_schema.models import SwarmKitTrigger

from swael_runtime.errors import ResolutionError
from swael_runtime.workspace import DiscoveredArtifact

from ._resolved import ResolvedTopology, ResolvedTrigger


def build_trigger_registry(
    artifacts: Iterable[DiscoveredArtifact],
    topologies: Mapping[str, ResolvedTopology],
) -> tuple[list[ResolvedTrigger], list[ResolutionError]]:
    errors: list[ResolutionError] = []
    triggers: list[ResolvedTrigger] = []
    seen_ids: set[str] = set()

    for artifact in artifacts:
        if artifact.kind != "trigger":
            continue
        try:
            model = SwarmKitTrigger.model_validate(dict(artifact.raw))
        except PydanticValidationError as exc:
            errors.append(
                ResolutionError(
                    code="trigger.model-construction",
                    message=(
                        f"trigger at {artifact.path} could not be constructed "
                        "as a pydantic SwarmKitTrigger model."
                    ),
                    artifact_path=artifact.path,
                    suggestion=f"pydantic raised: {exc.errors()[0]['msg']}",
                )
            )
            continue

        raw = dict(artifact.raw)
        metadata = raw.get("metadata", {}) or {}
        trigger_id = str(metadata.get("id", ""))
        if trigger_id in seen_ids:
            errors.append(
                ResolutionError(
                    code="trigger.duplicate-id",
                    message=(f"Trigger id {trigger_id!r} is declared twice."),
                    artifact_path=artifact.path,
                    yaml_pointer="/metadata/id",
                    suggestion="Rename one of the triggers.",
                )
            )
            continue
        seen_ids.add(trigger_id)

        targets_raw = raw.get("targets") or []
        validated_targets: list[str] = []
        for index, target_id in enumerate(targets_raw):
            target_str = str(target_id)
            validated_targets.append(target_str)
            if target_str not in topologies:
                errors.append(
                    ResolutionError(
                        code="trigger.unknown-target",
                        message=(
                            f"Trigger {trigger_id!r} targets topology "
                            f"{target_str!r} which is not defined in this "
                            "workspace."
                        ),
                        artifact_path=artifact.path,
                        yaml_pointer=f"/targets/{index}",
                        suggestion=(
                            f"Define a topology whose metadata.name is "
                            f"{target_str!r}, or change the trigger target "
                            "to an existing topology."
                        ),
                    )
                )

        triggers.append(
            ResolvedTrigger(
                id=trigger_id,
                raw=model,
                source_path=artifact.path,
                targets=tuple(validated_targets),
            )
        )

    return triggers, errors


__all__ = ["build_trigger_registry"]
