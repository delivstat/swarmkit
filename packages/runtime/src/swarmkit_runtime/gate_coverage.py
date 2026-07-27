"""Gate coverage — name the narrowest verified edge of a pipeline (read-only).

Static analysis over a resolved ``StageGraph`` + its funnels, slice 1 of
``design/details/gate-coverage-and-comprehension-debt.md``. No execution, no schema
change, no new runtime state — the coverage map is just the topology, re-projected.

Taxonomy (see the note): a ``Funnel`` always ends in a required human ``approve``, so a
stage with a ``gate:`` is always **human**; ``validate`` / ``judge`` / ``review`` are
pre-filter *strength* on top of that human gate. A stage with no ``gate:`` is
**passthrough** — the pipeline advances past it unverified by SwarmKit. Automated-only
gates come from ``decision_skills[]`` on nodes, not stage funnels, and are out of scope
for this pipeline-level view.

Both the ``swarmkit gates`` CLI and ``GET /pipelines/{id}/gate-coverage`` call
:func:`compute_gate_coverage` — one pure function, surfaced twice (Surface parity).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from swarmkit_runtime.resolver._resolved import ResolvedWorkspace

GateClass = Literal["passthrough", "human"]

#: The funnel pre-filter layers, weakest → strongest, that sit in front of the human gate.
PRE_FILTERS: tuple[str, ...] = ("validate", "judge", "review")


class UnknownPipelineError(KeyError):
    """Raised when a pipeline (StageGraph) id is not in the workspace."""


@dataclass(frozen=True)
class StageGate:
    """The gate on one stage's outgoing edge (how its output is verified before advancing)."""

    stage_id: str
    gate_class: GateClass
    funnel_id: str | None
    #: subset of :data:`PRE_FILTERS` present on the funnel, in weak→strong order
    pre_filters: tuple[str, ...]
    #: entered by an event no stage emits (CI / a rig / SAST) — informational
    external_entry: bool
    #: no downstream stage consumes this stage's ``success`` — nothing to gate onward
    terminal: bool
    #: the stage's plan-first `objective` (slice 8), or None if it declares none — a coverage gap
    objective: str | None = None

    @property
    def strength(self) -> int:
        """Order for 'narrowest': passthrough(0) < human+0(1) < … < human+3(4)."""
        if self.gate_class == "passthrough":
            return 0
        return 1 + len(self.pre_filters)


@dataclass(frozen=True)
class GateCoverage:
    """Gate coverage for one pipeline — every stage classified, weakest edge surfaced."""

    pipeline_id: str
    stages: tuple[StageGate, ...]

    @property
    def edges(self) -> tuple[StageGate, ...]:
        """Non-terminal stages — the ones with an onward edge to gate."""
        return tuple(s for s in self.stages if not s.terminal)

    @property
    def passthrough(self) -> tuple[StageGate, ...]:
        """Non-terminal stages that advance the pipeline with no SwarmKit gate."""
        return tuple(s for s in self.edges if s.gate_class == "passthrough")

    @property
    def narrowest(self) -> StageGate | None:
        """The weakest verified edge (passthrough first, then fewest pre-filters)."""
        edges = self.edges
        if not edges:
            return None
        return min(edges, key=lambda s: (s.strength, s.stage_id))

    def verdict(self) -> str:
        """A one-line human summary naming the narrowest verified edge."""
        n = self.narrowest
        if n is None:
            return f"pipeline '{self.pipeline_id}': no gateable edges (single or terminal-only)."
        if n.gate_class == "passthrough":
            extra = " (entered by an external event)" if n.external_entry else ""
            count = len(self.passthrough)
            tail = f"; {count} passthrough edge(s) total" if count > 1 else ""
            return (
                f"narrowest verified edge: stage '{n.stage_id}' advances with no gate "
                f"(passthrough){extra}{tail}."
            )
        pf = ", ".join(n.pre_filters) if n.pre_filters else "none"
        return (
            f"narrowest verified edge: stage '{n.stage_id}' is human-gated "
            f"(pre-filters: {pf}) — every edge is gated."
        )

    def violates(self, floor: GateClass) -> tuple[StageGate, ...]:
        """Stages below the required floor. Only ``human`` is meaningful: any passthrough edge."""
        if floor == "human":
            return self.passthrough
        return ()


def compute_gate_coverage(ws: ResolvedWorkspace, pipeline_id: str) -> GateCoverage:
    """Classify every stage edge of ``pipeline_id`` against its funnels. Pure + read-only."""
    sg = ws.stage_graphs.get(pipeline_id)
    if sg is None:
        raise UnknownPipelineError(pipeline_id)

    stages = sg.raw.stages
    emitted: set[str] = {s.success for s in stages if s.success}
    consumed: set[str] = {ev.root for s in stages for ev in (s.when or [])}

    results: list[StageGate] = []
    for s in stages:
        entry = [ev.root for ev in (s.when or [])]
        external_entry = bool(entry) and any(ev not in emitted for ev in entry)
        # Terminal when nothing downstream consumes this stage's success.
        terminal = s.success is None or s.success not in consumed
        objective = s.objective  # plan-first (slice 8): absent ⇒ a coverage gap

        if s.gate:
            funnel = ws.funnels.get(s.gate)
            pre: tuple[str, ...] = ()
            if funnel is not None:
                raw = funnel.raw
                pre = tuple(
                    name
                    for name, present in (
                        ("validate", raw.validate_ is not None),
                        ("judge", raw.judge is not None),
                        ("review", raw.review is not None),
                    )
                    if present
                )
            results.append(
                StageGate(s.id, "human", s.gate, pre, external_entry, terminal, objective)
            )
        else:
            results.append(
                StageGate(s.id, "passthrough", None, (), external_entry, terminal, objective)
            )

    return GateCoverage(pipeline_id, tuple(results))


def coverage_to_dict(cov: GateCoverage) -> dict[str, object]:
    """JSON-serializable coverage — the shared shape behind the CLI ``--json`` and the endpoint."""
    return {
        "pipeline": cov.pipeline_id,
        "verdict": cov.verdict(),
        "narrowest": cov.narrowest.stage_id if cov.narrowest else None,
        "stages": [
            {
                "stage": s.stage_id,
                "gate": s.gate_class,
                "funnel": s.funnel_id,
                "pre_filters": list(s.pre_filters),
                "external_entry": s.external_entry,
                "terminal": s.terminal,
                "objective": s.objective,
            }
            for s in cov.stages
        ],
    }


__all__ = [
    "PRE_FILTERS",
    "GateClass",
    "GateCoverage",
    "StageGate",
    "UnknownPipelineError",
    "compute_gate_coverage",
    "coverage_to_dict",
]
