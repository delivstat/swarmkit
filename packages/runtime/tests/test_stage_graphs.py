"""StageGraph discovery + resolution: registry, reference validation (slice 5)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.resolver import ResolvedStageGraph, resolve_workspace

_WORKSPACE = "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata:\n  id: ws\n  name: WS\n"

_FUNNEL = textwrap.dedent(
    """\
    apiVersion: swarmkit/v1
    kind: Funnel
    metadata:
      id: design-gate
      name: Design Gate
      description: A design sign-off gate for the pipeline stage.
    approve:
      rules:
        - scope: design:approve
          roles: [oms-lead]
          quorum: all
    provenance:
      authored_by: human
      version: 1.0.0
    """
)


def _topology(name: str) -> str:
    return textwrap.dedent(
        f"""\
        apiVersion: swarmkit/v1
        kind: Topology
        metadata:
          name: {name}
          version: 0.1.0
        agents:
          root:
            id: root-{name}
            role: root
        """
    )


def _scaffold(root: Path, stage_graph: str, *, topologies: list[str]) -> None:
    (root / "workspace.yaml").write_text(_WORKSPACE)
    (root / "funnels").mkdir()
    (root / "funnels" / "design-gate.yaml").write_text(_FUNNEL)
    (root / "topologies").mkdir()
    for name in topologies:
        (root / "topologies" / f"{name}.yaml").write_text(_topology(name))
    (root / "pipelines").mkdir()
    (root / "pipelines" / "pipeline.yaml").write_text(stage_graph)


_VALID_GRAPH = textwrap.dedent(
    """\
    apiVersion: swarmkit/v1
    kind: StageGraph
    metadata:
      id: sdlc
      name: SDLC
      description: intake then a gated design stage, with a defect loop back to design.
    stages:
      - id: intake
        topology: intake-topo
        when: [requirement.created]
        success: design.kickoff
      - id: design
        topology: design-topo
        when: [design.kickoff]
        gate: design-gate
        success: design.approved
    loops:
      - when: defect.raised
        to: design
    provenance:
      authored_by: human
      version: 1.0.0
    """
)


def test_stage_graph_resolves_with_valid_refs(tmp_path: Path) -> None:
    _scaffold(tmp_path, _VALID_GRAPH, topologies=["intake-topo", "design-topo"])
    ws = resolve_workspace(tmp_path)
    assert "sdlc" in ws.stage_graphs
    graph = ws.stage_graphs["sdlc"]
    assert isinstance(graph, ResolvedStageGraph)
    assert [s["id"] for s in graph.spec["stages"]] == ["intake", "design"]


def test_unknown_topology_ref_is_an_error(tmp_path: Path) -> None:
    _scaffold(tmp_path, _VALID_GRAPH, topologies=["intake-topo"])  # design-topo missing
    with pytest.raises(ResolutionErrors) as exc:
        resolve_workspace(tmp_path)
    assert "stage-graph.unknown-topology" in {e.code for e in exc.value.errors}


def test_unknown_loop_target_is_an_error(tmp_path: Path) -> None:
    bad = _VALID_GRAPH.replace("to: design", "to: nonexistent-stage")
    _scaffold(tmp_path, bad, topologies=["intake-topo", "design-topo"])
    with pytest.raises(ResolutionErrors) as exc:
        resolve_workspace(tmp_path)
    assert "stage-graph.unknown-loop-target" in {e.code for e in exc.value.errors}


def test_unknown_gate_ref_is_an_error(tmp_path: Path) -> None:
    bad = _VALID_GRAPH.replace("gate: design-gate", "gate: no-such-gate")
    _scaffold(tmp_path, bad, topologies=["intake-topo", "design-topo"])
    with pytest.raises(ResolutionErrors) as exc:
        resolve_workspace(tmp_path)
    assert "stage-graph.unknown-gate" in {e.code for e in exc.value.errors}
