"""Tests for the Skill Authoring Swarm reference topology (M7).

Validates resolution, archetype inheritance, knowledge-skill
assignments, and topology compilation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from swael_runtime.governance._mock import MockGovernanceProvider
from swael_runtime.langgraph_compiler import compile_topology
from swael_runtime.model_providers import MockModelProvider, ProviderRegistry
from swael_runtime.resolver import resolve_workspace
from swael_schema import validate

REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_WS = REPO_ROOT / "reference"


# ---- schema validation --------------------------------------------------


@pytest.mark.parametrize(
    "archetype_file",
    sorted(
        f
        for f in (REFERENCE_WS / "archetypes").glob("*.yaml")
        if f.stem
        in {
            "authoring-supervisor",
            "conversation-leader",
            "knowledge-searcher",
            "schema-drafter",
            "artifact-validator",
            "test-writer",
            "artifact-publisher",
        }
    ),
    ids=lambda p: p.stem,
)
def test_authoring_archetype_validates(archetype_file: Path) -> None:
    data = yaml.safe_load(archetype_file.read_text())
    validate("archetype", data)


@pytest.mark.parametrize(
    "skill_file",
    sorted(
        f
        for f in (REFERENCE_WS / "skills").glob("*.yaml")
        if f.stem in {"list-reference-skills", "get-schema", "validate-workspace"}
    ),
    ids=lambda p: p.stem,
)
def test_authoring_skill_validates(skill_file: Path) -> None:
    data = yaml.safe_load(skill_file.read_text())
    validate("skill", data)


# ---- workspace resolution -----------------------------------------------


def test_skill_authoring_topology_resolves() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    assert "skill-authoring" in workspace.topologies


def test_skill_authoring_has_conversation_leader() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    topology = workspace.topologies["skill-authoring"]
    children = {c.id for c in topology.root.children}
    assert "conversation-leader" in children


def test_conversation_leader_has_five_workers() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    topology = workspace.topologies["skill-authoring"]
    leader = next(c for c in topology.root.children if c.id == "conversation-leader")
    worker_ids = {c.id for c in leader.children}
    assert worker_ids == {
        "knowledge-searcher",
        "schema-drafter",
        "validator",
        "test-writer",
        "publisher",
    }


def test_knowledge_searcher_has_knowledge_skills() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    topology = workspace.topologies["skill-authoring"]
    leader = next(c for c in topology.root.children if c.id == "conversation-leader")
    searcher = next(c for c in leader.children if c.id == "knowledge-searcher")
    skill_ids = {s.id for s in searcher.skills}
    assert "query-swael-docs" in skill_ids
    assert "list-reference-skills" in skill_ids
    assert "validate-workspace" in skill_ids


def test_validator_has_validation_skills() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    topology = workspace.topologies["skill-authoring"]
    leader = next(c for c in topology.root.children if c.id == "conversation-leader")
    validator = next(c for c in leader.children if c.id == "validator")
    skill_ids = {s.id for s in validator.skills}
    assert "validate-workspace" in skill_ids
    assert "query-swael-docs" in skill_ids


# ---- topology compilation -----------------------------------------------


def test_skill_authoring_topology_compiles() -> None:
    workspace = resolve_workspace(REFERENCE_WS)
    topology = workspace.topologies["skill-authoring"]
    registry = ProviderRegistry()
    registry.register(MockModelProvider())
    governance = MockGovernanceProvider(allow_all=True)
    graph = compile_topology(topology, provider_registry=registry, governance=governance)
    assert graph is not None
