"""Tests for the Code Review Swarm reference topology (M6).

Validates resolution, archetype inheritance, skill assignments,
and topology compilation. Live execution is a demo target.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers import MockModelProvider, ProviderRegistry
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_schema import validate

REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_WS = REPO_ROOT / "reference"


# ---- schema validation --------------------------------------------------


@pytest.mark.parametrize(
    "skill_file",
    sorted((REFERENCE_WS / "skills").glob("*.yaml")),
    ids=lambda p: p.stem,
)
def test_reference_skill_validates(skill_file: Path) -> None:
    data = yaml.safe_load(skill_file.read_text())
    validate("skill", data)


@pytest.mark.parametrize(
    "archetype_file",
    sorted((REFERENCE_WS / "archetypes").glob("*.yaml")),
    ids=lambda p: p.stem,
)
def test_reference_archetype_validates(archetype_file: Path) -> None:
    data = yaml.safe_load(archetype_file.read_text())
    validate("archetype", data)


@pytest.mark.parametrize(
    "topology_file",
    sorted((REFERENCE_WS / "topologies").glob("*.yaml")),
    ids=lambda p: p.stem,
)
def test_reference_topology_validates(topology_file: Path) -> None:
    data = yaml.safe_load(topology_file.read_text())
    validate("topology", data)


# ---- workspace resolution -----------------------------------------------


def test_reference_workspace_resolves() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    assert "code-review" in workspace.topologies
    assert len(workspace.skills) == 12
    assert len(workspace.archetypes) == 16


def test_code_review_topology_has_three_leaders() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    topology = workspace.topologies["code-review"]
    leader_ids = {c.id for c in topology.root.children}
    assert leader_ids == {"engineering-leader", "qa-leader", "ops-leader"}


def test_engineering_leader_has_three_workers() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    topology = workspace.topologies["code-review"]
    eng = next(c for c in topology.root.children if c.id == "engineering-leader")
    worker_ids = {c.id for c in eng.children}
    assert worker_ids == {"code-reader", "code-reviewer", "security-reviewer"}


def test_code_reader_has_github_skills() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    topology = workspace.topologies["code-review"]
    eng = next(c for c in topology.root.children if c.id == "engineering-leader")
    reader = next(c for c in eng.children if c.id == "code-reader")
    skill_ids = {s.id for s in reader.skills}
    assert skill_ids == {"github-repo-read", "github-pr-read"}


def test_deploy_reviewer_overrides_llm_judge_skills() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    topology = workspace.topologies["code-review"]
    ops = next(c for c in topology.root.children if c.id == "ops-leader")
    deployer = next(c for c in ops.children if c.id == "deploy-reviewer")
    skill_ids = {s.id for s in deployer.skills}
    assert "deploy-risk-review" in skill_ids


# ---- topology compilation -----------------------------------------------


def test_code_review_topology_compiles() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    topology = workspace.topologies["code-review"]
    registry = ProviderRegistry()
    registry.register(MockModelProvider())
    governance = MockGovernanceProvider(allow_all=True)
    graph = compile_topology(topology, provider_registry=registry, governance=governance)
    assert graph is not None
