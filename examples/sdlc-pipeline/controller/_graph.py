"""The stage-graph as a small domain object the controller interprets.

Built from a resolved StageGraph ``spec`` (the runtime ref-checks it; the controller consumes
the verified data) — so a different pipeline is new *data*, not new controller code, keeping the
topology-as-data spirit at the sequencing layer. This module has **no** dependency on the
SwarmKit runtime; the controller is a self-contained service. See
design/details/pipeline-controller.md.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Stage:
    """One bounded stage of the pipeline (design "Stage-graph (the pipeline as data)")."""

    id: str
    topology: str
    when: tuple[str, ...]
    success: str | None
    locks: tuple[str, ...]
    release_locks_on: str | None
    gate: str | None
    compensation: str | None


@dataclass(frozen=True)
class Loop:
    """A cross-stage edge — the defect cycle: ``when`` routes to stage ``to``."""

    when: str
    to: str


@dataclass(frozen=True)
class Route:
    """The result of routing an inbound event to a stage."""

    stage: Stage
    is_loop: bool


class StageGraph:
    """A validated stage-graph the controller sequences as a saga."""

    def __init__(self, graph_id: str, stages: Sequence[Stage], loops: Sequence[Loop]) -> None:
        self.id = graph_id
        self.stages: tuple[Stage, ...] = tuple(stages)
        self.loops: tuple[Loop, ...] = tuple(loops)
        self._by_id: dict[str, Stage] = {s.id: s for s in self.stages}

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any]) -> StageGraph:
        """Build from a resolved StageGraph ``spec`` mapping (the schema-validated raw dict)."""
        graph_id = str((spec.get("metadata") or {}).get("id", ""))
        stages = [
            Stage(
                id=str(raw["id"]),
                topology=str(raw["topology"]),
                when=tuple(raw.get("when", []) or []),
                success=raw.get("success"),
                locks=tuple(raw.get("locks", []) or []),
                release_locks_on=raw.get("release_locks_on"),
                gate=raw.get("gate"),
                compensation=raw.get("compensation"),
            )
            for raw in spec.get("stages", [])
        ]
        loops = [
            Loop(when=str(raw["when"]), to=str(raw["to"])) for raw in spec.get("loops", []) or []
        ]
        return cls(graph_id, stages, loops)

    def stage(self, stage_id: str) -> Stage:
        return self._by_id[stage_id]

    def route(self, event: str) -> Route | None:
        """Route an inbound event to a stage.

        A stage's ``when`` is the normal forward edge; a ``loops[].when`` is a defect-cycle
        re-entry. Normal edges and loop edges are disjoint by construction, but a loop is
        flagged so the controller re-runs an already-passed stage only for the defect cycle.
        """
        for stage in self.stages:
            if event in stage.when:
                return Route(stage=stage, is_loop=False)
        for loop in self.loops:
            if loop.when == event:
                return Route(stage=self._by_id[loop.to], is_loop=True)
        return None


__all__ = ["Loop", "Route", "Stage", "StageGraph"]
