"""Funnel artifact discovery + resolution: registry, node ref binding, errors."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.resolver import ResolvedFunnel, resolve_workspace

_WORKSPACE = "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata:\n  id: ws\n  name: WS\n"

_FUNNEL = textwrap.dedent(
    """\
    apiVersion: swarmkit/v1
    kind: Funnel
    metadata:
      id: design-gate
      name: Design Gate
      description: Judge the design then a lead signs off before it advances.
    judge:
      skill: artifact-judge
      threshold: 0.8
      max_retries: 2
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


def _topology(funnel_ref: str) -> str:
    return textwrap.dedent(
        f"""\
        apiVersion: swarmkit/v1
        kind: Topology
        metadata:
          name: t
          version: 0.1.0
        agents:
          root:
            id: coordinator
            role: root
            children:
              - id: designer
                role: worker
                funnel: {funnel_ref}
        """
    )


def _scaffold(root: Path, *, funnel: str, funnel_ref: str) -> None:
    (root / "workspace.yaml").write_text(_WORKSPACE)
    (root / "funnels").mkdir()
    (root / "funnels" / "design-gate.yaml").write_text(funnel)
    (root / "topologies").mkdir()
    (root / "topologies" / "t.yaml").write_text(_topology(funnel_ref))


def test_funnel_is_discovered_and_bound_to_node(tmp_path: Path) -> None:
    _scaffold(tmp_path, funnel=_FUNNEL, funnel_ref="design-gate")
    ws = resolve_workspace(tmp_path)

    assert "design-gate" in ws.funnels
    funnel = ws.funnels["design-gate"]
    assert isinstance(funnel, ResolvedFunnel)
    assert funnel.spec["judge"]["threshold"] == 0.8

    designer = ws.topologies["t"].root.children[0]
    assert designer.id == "designer"
    assert designer.funnel is not None
    assert designer.funnel.id == "design-gate"


def test_unknown_funnel_reference_is_an_error(tmp_path: Path) -> None:
    _scaffold(tmp_path, funnel=_FUNNEL, funnel_ref="nonexistent-gate")
    with pytest.raises(ResolutionErrors) as excinfo:
        resolve_workspace(tmp_path)
    codes = {e.code for e in excinfo.value.errors}
    assert "agent.unknown-funnel" in codes


def test_node_without_funnel_resolves_to_none(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text(_WORKSPACE)
    (tmp_path / "topologies").mkdir()
    (tmp_path / "topologies" / "t.yaml").write_text(
        textwrap.dedent(
            """\
            apiVersion: swarmkit/v1
            kind: Topology
            metadata:
              name: t
              version: 0.1.0
            agents:
              root:
                id: coordinator
                role: root
            """
        )
    )
    ws = resolve_workspace(tmp_path)
    assert ws.topologies["t"].root.funnel is None
    assert ws.funnels == {}
