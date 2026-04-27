"""Tests for the end-to-end workspace resolver (M1.5)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from swael_runtime.resolver import (
    ResolutionErrors,
    ResolvedAgent,
    ResolvedTopology,
    ResolvedTrigger,
    ResolvedWorkspace,
    resolve_workspace,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VALID = FIXTURES / "workspaces"
INVALID = FIXTURES / "workspaces-invalid"


# ---- happy paths ---------------------------------------------------------


def test_resolve_full_workspace() -> None:
    ws = resolve_workspace(VALID / "full")
    assert isinstance(ws, ResolvedWorkspace)
    assert ws.raw.metadata.id == "full-workspace"
    assert list(ws.topologies.keys()) == ["hello"]
    topology = ws.topologies["hello"]
    assert isinstance(topology, ResolvedTopology)
    assert topology.root.id == "root"
    assert topology.root.role == "root"
    assert topology.root.source_archetype == "supervisor-root"
    # Archetype's model block passes through to the agent.
    assert topology.root.model == {
        "provider": "anthropic",
        "name": "claude-sonnet-4-6",
    }


def test_resolve_minimal_workspace() -> None:
    # Minimal has no topologies — the registry is empty but resolution
    # still succeeds.
    ws = resolve_workspace(VALID / "minimal")
    assert ws.topologies == {}
    assert ws.skills == {}
    assert ws.archetypes == {}
    assert ws.triggers == ()


def test_resolve_triggers_validated_against_topologies() -> None:
    ws = resolve_workspace(VALID / "full")
    # full workspace has one topology "hello" and two triggers targeting it.
    ids = {t.id for t in ws.triggers}
    assert ids == {"hello-webhook", "hello-daily"}
    for trig in ws.triggers:
        assert isinstance(trig, ResolvedTrigger)
        assert trig.targets == ("hello",)


def test_resolve_is_deterministic() -> None:
    a = resolve_workspace(VALID / "full")
    b = resolve_workspace(VALID / "full")
    assert list(a.topologies.keys()) == list(b.topologies.keys())
    assert list(a.skills.keys()) == list(b.skills.keys())
    assert list(a.archetypes.keys()) == list(b.archetypes.keys())


def test_resolved_tree_merges_archetype_with_agent_overrides() -> None:
    ws = resolve_workspace(VALID / "resolved-tree")
    topology = ws.topologies["review"]
    root = topology.root
    # Root agent uses supervisor-root archetype + a temperature override.
    assert root.model == {
        "provider": "anthropic",
        "name": "claude-opus-4-7",
        "temperature": 0.3,  # agent-level override wins
    }
    # Three-way: two children, archetype + additional + direct.
    assert len(root.children) == 2
    reviewer, independent = root.children
    assert isinstance(reviewer, ResolvedAgent)
    assert reviewer.source_archetype == "code-review-worker"
    # skills_additional appended: archetype's two + one extra.
    reviewer_skill_ids = [s.id for s in reviewer.skills]
    assert reviewer_skill_ids == [
        "github-repo-read",
        "code-quality-review",
        "github-repo-read",
    ]
    # Independent worker has no archetype; model declared inline.
    assert independent.source_archetype is None
    assert independent.model == {"provider": "openai", "name": "gpt-4o"}
    assert [s.id for s in independent.skills] == ["code-quality-review"]


def test_resolved_agent_is_frozen() -> None:
    ws = resolve_workspace(VALID / "full")
    topology = ws.topologies["hello"]
    root = topology.root
    with pytest.raises(FrozenInstanceError):
        root.id = "x"  # type: ignore[misc]


# ---- negative paths ------------------------------------------------------


def test_missing_workspace_root_raises_resolution_errors() -> None:
    with pytest.raises(ResolutionErrors) as excinfo:
        resolve_workspace("/nonexistent/path/nowhere")
    codes = [e.code for e in excinfo.value]
    assert codes == ["workspace.not-found"]


def test_unknown_archetype_surfaces_error() -> None:
    with pytest.raises(ResolutionErrors) as excinfo:
        resolve_workspace(INVALID / "unknown-archetype")
    codes = [e.code for e in excinfo.value]
    assert "agent.unknown-archetype" in codes


def test_abstract_no_match_surfaces_error() -> None:
    with pytest.raises(ResolutionErrors) as excinfo:
        resolve_workspace(INVALID / "abstract-no-match")
    codes = [e.code for e in excinfo.value]
    assert "agent.abstract-no-match" in codes


def test_abstract_ambiguous_surfaces_error() -> None:
    with pytest.raises(ResolutionErrors) as excinfo:
        resolve_workspace(INVALID / "abstract-ambiguous")
    codes = [e.code for e in excinfo.value]
    assert "agent.abstract-ambiguous" in codes
    # The message should list both candidate skill IDs.
    err = next(e for e in excinfo.value if e.code == "agent.abstract-ambiguous")
    assert "content-review-strict" in err.message
    assert "content-review-lenient" in err.message


def test_duplicate_agent_id_surfaces_error() -> None:
    with pytest.raises(ResolutionErrors) as excinfo:
        resolve_workspace(INVALID / "duplicate-agent-id")
    codes = [e.code for e in excinfo.value]
    assert "agent.duplicate-id" in codes


def test_trigger_unknown_target_surfaces_error() -> None:
    with pytest.raises(ResolutionErrors) as excinfo:
        resolve_workspace(INVALID / "trigger-unknown-target")
    codes = [e.code for e in excinfo.value]
    assert "trigger.unknown-target" in codes
