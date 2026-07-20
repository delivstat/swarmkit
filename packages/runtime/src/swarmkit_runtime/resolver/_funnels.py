"""Funnel resolution — builds the workspace funnel registry.

A Funnel is a reusable per-artifact quality gate (design/details/gate-funnel.md),
a first-class artifact referenced by id from a topology node's ``funnel:`` field.
Funnels are resolved before topologies so a node's funnel reference can be verified
against this registry during topology resolution.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError as PydanticValidationError
from swarmkit_schema.models import SwarmKitFunnel

from swarmkit_runtime.errors import ResolutionError
from swarmkit_runtime.workspace import DiscoveredArtifact

from ._resolved import ResolvedFunnel


def build_funnel_registry(
    artifacts: Iterable[DiscoveredArtifact],
) -> tuple[dict[str, ResolvedFunnel], list[ResolutionError]]:
    """Build the ``id -> ResolvedFunnel`` registry from discovered funnel artifacts.

    Each funnel has already passed JSON-Schema validation in ``validate_discovered``;
    here we construct the typed model, enforce id uniqueness, and surface the funnel for
    the topology resolver (node ``funnel:`` refs) and the compiler (gate subgraph).
    """
    errors: list[ResolutionError] = []
    funnels: dict[str, ResolvedFunnel] = {}

    for artifact in artifacts:
        if artifact.kind != "funnel":
            continue

        raw = dict(artifact.raw)
        try:
            model = SwarmKitFunnel.model_validate(raw)
        except PydanticValidationError as exc:
            errors.append(
                ResolutionError(
                    code="funnel.model-construction",
                    message=(
                        f"funnel at {artifact.path} could not be constructed "
                        "as a pydantic SwarmKitFunnel model."
                    ),
                    artifact_path=artifact.path,
                    suggestion=f"pydantic raised: {exc.errors()[0]['msg']}",
                )
            )
            continue

        metadata = raw.get("metadata", {}) or {}
        funnel_id = str(metadata.get("id", ""))
        if funnel_id in funnels:
            errors.append(
                ResolutionError(
                    code="funnel.duplicate-id",
                    message=f"Funnel id {funnel_id!r} is declared twice.",
                    artifact_path=artifact.path,
                    yaml_pointer="/metadata/id",
                    suggestion="Rename one of the funnels so every id is unique.",
                )
            )
            continue

        funnels[funnel_id] = ResolvedFunnel(
            id=funnel_id,
            raw=model,
            source_path=artifact.path,
            spec=raw,
        )

    return funnels, errors


__all__ = ["build_funnel_registry"]
