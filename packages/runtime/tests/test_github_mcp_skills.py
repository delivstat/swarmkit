"""Tests for the GitHub MCP reference skills (M5).

Proves the reference skills validate, the fixture workspace resolves,
and the topology compiles — without hitting the real GitHub API.
Live end-to-end execution is a demo target, not a CI gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers import MockModelProvider, ProviderRegistry
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_schema import validate

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "workspaces"
GITHUB_WS = FIXTURES / "github-mcp"
REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_SKILLS = REPO_ROOT / "reference" / "skills"


# ---- schema validation (reference/ skills) ------------------------------


@pytest.mark.parametrize(
    "skill_file",
    sorted(REFERENCE_SKILLS.glob("github-*.yaml")),
    ids=lambda p: p.stem,
)
def test_reference_skill_validates(skill_file: Path) -> None:
    """Every github-* reference skill passes JSON Schema validation."""
    import yaml  # noqa: PLC0415

    data = yaml.safe_load(skill_file.read_text())
    validate("skill", data)


# ---- workspace resolution -----------------------------------------------


def test_github_mcp_workspace_resolves() -> None:
    """The github-mcp fixture workspace resolves without errors."""
    workspace = resolve_workspace(GITHUB_WS)
    assert "github-repo-read" in workspace.skills
    assert "github-pr-read" in workspace.skills
    assert "github-issue-read" in workspace.skills
    assert "github-reader" in workspace.archetypes
    assert "github-read" in workspace.topologies


def test_github_mcp_skills_reference_correct_server() -> None:
    """Each github skill targets the 'github' MCP server."""
    workspace = resolve_workspace(GITHUB_WS)
    for skill_id in ("github-repo-read", "github-pr-read", "github-issue-read"):
        skill = workspace.skills[skill_id]
        impl = skill.raw.implementation
        server = impl.get("server") if isinstance(impl, dict) else getattr(impl, "server", "")
        assert server == "github", f"{skill_id} should target 'github', got '{server}'"


def test_github_reader_archetype_inherits_all_skills() -> None:
    """The github-reader archetype references all three GitHub skills."""
    workspace = resolve_workspace(GITHUB_WS)
    topology = workspace.topologies["github-read"]
    reader = next(a for a in topology.root.children if a.id == "reader")
    skill_ids = {s.id for s in reader.skills}
    assert skill_ids == {"github-repo-read", "github-pr-read", "github-issue-read"}


# ---- topology compilation -----------------------------------------------


def test_github_mcp_topology_compiles() -> None:
    """The github-read topology compiles into a LangGraph graph."""
    workspace = resolve_workspace(GITHUB_WS)
    topology = workspace.topologies["github-read"]
    registry = ProviderRegistry()
    registry.register(MockModelProvider())
    governance = MockGovernanceProvider(allow_all=True)
    graph = compile_topology(topology, provider_registry=registry, governance=governance)
    assert graph is not None
