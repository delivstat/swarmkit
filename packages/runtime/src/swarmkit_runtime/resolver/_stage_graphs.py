"""StageGraph resolution — verifies every stage graph's references.

A StageGraph (design/details/pipeline-controller.md) is the pipeline as data. The runtime
does not execute it — the reference controller does — but it is a workspace artifact, so
resolution verifies its references (each stage's ``topology`` / ``compensation`` exist, its
``gate`` is a known Funnel, stage ids are unique, and every ``loops[].to`` names a stage in
the graph). Consumed by the controller as a verified graph.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from swarmkit_schema.models import SwarmKitStageGraph

from swarmkit_runtime.errors import ResolutionError
from swarmkit_runtime.workspace import DiscoveredArtifact

from ._resolved import ResolvedFunnel, ResolvedStageGraph, ResolvedTopology


def build_stage_graph_registry(
    artifacts: Iterable[DiscoveredArtifact],
    topologies: Mapping[str, ResolvedTopology],
    funnels: Mapping[str, ResolvedFunnel],
) -> tuple[dict[str, ResolvedStageGraph], list[ResolutionError]]:
    """Build the ``id -> ResolvedStageGraph`` registry, verifying every reference."""
    errors: list[ResolutionError] = []
    graphs: dict[str, ResolvedStageGraph] = {}

    for artifact in artifacts:
        if artifact.kind != "stage-graph":
            continue
        raw = dict(artifact.raw)
        try:
            model = SwarmKitStageGraph.model_validate(raw)
        except PydanticValidationError as exc:
            errors.append(
                ResolutionError(
                    code="stage-graph.model-construction",
                    message=(
                        f"stage graph at {artifact.path} could not be constructed "
                        "as a pydantic SwarmKitStageGraph model."
                    ),
                    artifact_path=artifact.path,
                    suggestion=f"pydantic raised: {exc.errors()[0]['msg']}",
                )
            )
            continue

        graph_id = str((raw.get("metadata") or {}).get("id", ""))
        if graph_id in graphs:
            errors.append(
                ResolutionError(
                    code="stage-graph.duplicate-id",
                    message=f"StageGraph id {graph_id!r} is declared twice.",
                    artifact_path=artifact.path,
                    yaml_pointer="/metadata/id",
                    suggestion="Rename one of the stage graphs so every id is unique.",
                )
            )
            continue

        errors.extend(_check_refs(raw, artifact, topologies, funnels))
        graphs[graph_id] = ResolvedStageGraph(
            id=graph_id, raw=model, source_path=artifact.path, spec=raw
        )

    return graphs, errors


def _check_refs(
    raw: Mapping[str, Any],
    artifact: DiscoveredArtifact,
    topologies: Mapping[str, ResolvedTopology],
    funnels: Mapping[str, ResolvedFunnel],
) -> list[ResolutionError]:
    errors: list[ResolutionError] = []
    stages = raw.get("stages") or []
    stage_ids: set[str] = set()

    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        sid = str(stage.get("id", ""))
        base = f"/stages/{index}"
        if sid in stage_ids:
            errors.append(
                _err(
                    "stage-graph.duplicate-stage",
                    f"Stage id {sid!r} appears twice.",
                    artifact,
                    f"{base}/id",
                )
            )
        stage_ids.add(sid)

        topology = stage.get("topology")
        if topology is not None and str(topology) not in topologies:
            errors.append(
                _err(
                    "stage-graph.unknown-topology",
                    f"Stage {sid!r} references topology {str(topology)!r} not in this workspace.",
                    artifact,
                    f"{base}/topology",
                )
            )
        comp = stage.get("compensation")
        if comp is not None and str(comp) not in topologies:
            errors.append(
                _err(
                    "stage-graph.unknown-compensation",
                    f"Stage {sid!r} compensation topology {str(comp)!r} not in this workspace.",
                    artifact,
                    f"{base}/compensation",
                )
            )
        gate = stage.get("gate")
        if gate is not None and str(gate) not in funnels:
            errors.append(
                _err(
                    "stage-graph.unknown-gate",
                    f"Stage {sid!r} references gate {str(gate)!r} which is not a Funnel here.",
                    artifact,
                    f"{base}/gate",
                )
            )

    loops = raw.get("loops") or []
    for index, loop in enumerate(loops):
        if not isinstance(loop, dict):
            continue
        target = loop.get("to")
        if target is not None and str(target) not in stage_ids:
            errors.append(
                _err(
                    "stage-graph.unknown-loop-target",
                    f"Loop routes to stage {str(target)!r} which is not defined in this graph.",
                    artifact,
                    f"/loops/{index}/to",
                )
            )

    return errors


def _err(code: str, message: str, artifact: DiscoveredArtifact, pointer: str) -> ResolutionError:
    return ResolutionError(
        code=code, message=message, artifact_path=artifact.path, yaml_pointer=pointer
    )


__all__ = ["build_stage_graph_registry"]
