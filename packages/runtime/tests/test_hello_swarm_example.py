"""Smoke tests for the ``examples/hello-swarm/`` on-ramp example.

These guard the M1 exit demo — a refactor that breaks the example's
resolution is worse than a refactor that breaks a test fixture, because
it's what new users actually see. See
``design/details/hello-swarm-example.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.resolver import resolve_workspace

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = REPO_ROOT / "examples" / "hello-swarm"


def test_hello_swarm_valid_workspace_resolves() -> None:
    ws = resolve_workspace(EXAMPLE / "workspace")

    assert str(ws.raw.metadata.id) == "hello-swarm"
    assert set(ws.topologies) == {"hello"}
    assert set(ws.archetypes) == {"greeter"}
    assert set(ws.skills) == {"say-hello"}

    topology = ws.topologies["hello"]
    assert topology.root.id == "root"
    assert [c.id for c in topology.root.children] == ["greeter"]

    child = topology.root.children[0]
    assert child.source_archetype == "greeter"
    # Archetype defaults propagated onto the child.
    assert child.model is not None
    assert child.model["provider"] == "anthropic"
    assert [s.id for s in child.skills] == ["say-hello"]


def test_hello_swarm_broken_workspace_surfaces_unknown_archetype() -> None:
    with pytest.raises(ResolutionErrors) as excinfo:
        resolve_workspace(EXAMPLE / "workspace-broken")

    errors = list(excinfo.value)
    assert len(errors) == 1
    err = errors[0]
    assert err.code == "agent.unknown-archetype"
    # The typo — 'greter' — must appear in the message so the reader can
    # connect the error to the source line without opening the design doc.
    assert "greter" in err.message
    assert err.yaml_pointer == "/agents/root/children/0/archetype"
