"""Tests for the workspace discovery layer (M1.2)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from swarmkit_runtime.workspace import (
    ArtifactKindMismatchError,
    DeepNestingError,
    DiscoveredArtifact,
    MissingWorkspaceFileError,
    WorkspaceNotFoundError,
    YAMLParseError,
    discover,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VALID = FIXTURES / "workspaces"
INVALID = FIXTURES / "workspaces-invalid"


# ---- valid workspaces ------------------------------------------------------


def test_minimal_workspace_discovers_only_workspace_yaml() -> None:
    artifacts = discover(VALID / "minimal")
    assert len(artifacts) == 1
    (only,) = artifacts
    assert only.kind == "workspace"
    assert only.path.name == "workspace.yaml"
    assert only.raw["metadata"]["id"] == "minimal-workspace"


def test_full_workspace_discovers_every_directory() -> None:
    artifacts = discover(VALID / "full")
    kinds = [a.kind for a in artifacts]
    # Workspace comes first; the rest are in directory order (sorted).
    assert kinds[0] == "workspace"
    assert sorted(kinds[1:]) == sorted(["archetype", "skill", "topology", "trigger", "trigger"])


def test_full_workspace_is_deterministic() -> None:
    a = discover(VALID / "full")
    b = discover(VALID / "full")
    assert [x.path for x in a] == [x.path for x in b]
    assert [x.kind for x in a] == [x.kind for x in b]


def test_full_workspace_includes_schedules_as_trigger_kind() -> None:
    artifacts = discover(VALID / "full")
    trigger_paths = [a.path.name for a in artifacts if a.kind == "trigger"]
    assert "daily.yaml" in trigger_paths
    assert "webhook.yaml" in trigger_paths


def test_grouped_skills_recurses_one_level() -> None:
    artifacts = discover(VALID / "grouped-skills")
    skill_ids = sorted(a.raw["metadata"]["id"] for a in artifacts if a.kind == "skill")
    assert skill_ids == ["github-repo-read", "llm-judge"]


def test_discovered_artifact_is_frozen() -> None:
    artifacts = discover(VALID / "minimal")
    with pytest.raises(FrozenInstanceError):
        artifacts[0].kind = "topology"  # type: ignore[misc]


# ---- invalid workspaces ----------------------------------------------------


def test_missing_workspace_root_raises() -> None:
    with pytest.raises(WorkspaceNotFoundError):
        discover(INVALID / "does-not-exist-anywhere")


def test_missing_workspace_yaml_raises() -> None:
    with pytest.raises(MissingWorkspaceFileError) as excinfo:
        discover(INVALID / "missing-workspace-yaml")
    assert "workspace.yaml" in str(excinfo.value)


def test_yaml_parse_error_raises_with_line_hint() -> None:
    with pytest.raises(YAMLParseError) as excinfo:
        discover(INVALID / "yaml-parse-error")
    err = excinfo.value
    assert err.path.name == "broken.yaml"
    assert err.line is not None


def test_kind_mismatch_raises() -> None:
    with pytest.raises(ArtifactKindMismatchError) as excinfo:
        discover(INVALID / "kind-mismatch")
    err = excinfo.value
    assert err.expected_kind == "Skill"
    assert err.actual_kind == "Topology"


def test_deep_nesting_raises() -> None:
    with pytest.raises(DeepNestingError) as excinfo:
        discover(INVALID / "deep-nesting")
    assert "too-deep.yaml" in str(excinfo.value)


# ---- behaviour details -----------------------------------------------------


def _write_workspace(ws: Path, name: str) -> None:
    (ws / "workspace.yaml").write_text(
        f"apiVersion: swarmkit/v1\nkind: Workspace\n"
        f"metadata:\n  id: {name}\n  name: {name.replace('-', ' ').title()}\n",
        encoding="utf-8",
    )


def _write_skill(path: Path, skill_id: str) -> None:
    path.write_text(
        "apiVersion: swarmkit/v1\n"
        "kind: Skill\n"
        "metadata:\n"
        f"  id: {skill_id}\n"
        f"  name: {skill_id}\n"
        "  description: fixture skill\n"
        "category: capability\n"
        "implementation:\n"
        "  type: mcp_tool\n"
        "  server: x\n"
        "  tool: y\n"
        "provenance:\n"
        "  authored_by: human\n"
        "  version: 0.0.1\n",
        encoding="utf-8",
    )


def test_ignores_hidden_files(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_workspace(ws, "hidden-test")
    skills = ws / "skills"
    skills.mkdir()
    _write_skill(skills / ".hidden.yaml", "should-not-see")
    artifacts = discover(ws)
    assert sum(1 for a in artifacts if a.kind == "skill") == 0


def test_ignores_non_yaml_files(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_workspace(ws, "non-yaml")
    skills = ws / "skills"
    skills.mkdir()
    (skills / "readme.md").write_text("# not a skill\n")
    (skills / "notes.txt").write_text("ignore me\n")
    artifacts = discover(ws)
    assert all(a.kind != "skill" for a in artifacts)


def test_ignores_swarmkit_state_directory(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_workspace(ws, "state-ignored")
    swarmkit_state = ws / ".swarmkit" / "state"
    swarmkit_state.mkdir(parents=True)
    (swarmkit_state / "stale.yaml").write_text("anything: goes\n")
    artifacts = discover(ws)
    assert len(artifacts) == 1
    assert artifacts[0].kind == "workspace"


def test_accepts_yml_as_well_as_yaml(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yml").write_text(
        "apiVersion: swarmkit/v1\n"
        "kind: Workspace\n"
        "metadata:\n"
        "  id: yml-suffix\n"
        "  name: YML Suffix\n",
        encoding="utf-8",
    )
    artifacts = discover(ws)
    assert artifacts[0].path.suffix == ".yml"


def test_returns_discovered_artifact_instances() -> None:
    artifacts = discover(VALID / "minimal")
    assert all(isinstance(a, DiscoveredArtifact) for a in artifacts)
