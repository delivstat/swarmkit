"""Tests for the validation layer (M1.3).

Exercises three things:
  1. ``validate_discovered`` runs schema validation across a list of
     ``DiscoveredArtifact`` and produces structured errors (no
     short-circuit).
  2. ``resolution_error_from_discovery`` converts every DiscoveryError
     subclass into a ResolutionError with a useful suggestion.
  3. ``ResolutionError`` / ``ResolutionErrors`` shape invariants
     (frozen, iterable, non-empty).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
import yaml as pyyaml
from swael_runtime.errors import (
    ResolutionError,
    ResolutionErrors,
    yaml_pointer,
)
from swael_runtime.resolver import (
    errors_or_raise,
    resolution_error_from_discovery,
    validate_discovered,
)
from swael_runtime.workspace import (
    ArtifactKindMismatchError,
    DeepNestingError,
    DiscoveredArtifact,
    MalformedArtifactError,
    MissingWorkspaceFileError,
    WorkspaceNotFoundError,
    YAMLParseError,
    discover,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VALID = FIXTURES / "workspaces"


# ---- ResolutionError shape -----------------------------------------------


def test_resolution_error_is_frozen() -> None:
    err = ResolutionError(code="x.y", message="m", artifact_path=Path("/a/b.yaml"))
    with pytest.raises(FrozenInstanceError):
        err.code = "z"  # type: ignore[misc]


def test_resolution_errors_rejects_empty() -> None:
    with pytest.raises(ValueError):
        ResolutionErrors([])


def test_resolution_errors_is_iterable() -> None:
    e = ResolutionError(code="x", message="m", artifact_path=Path("/a"))
    agg = ResolutionErrors([e, e])
    assert list(agg) == [e, e]
    assert len(agg) == 2


# ---- yaml_pointer -------------------------------------------------------


def test_yaml_pointer_empty_path_is_empty_string() -> None:
    assert yaml_pointer([]) == ""


def test_yaml_pointer_builds_json_pointer() -> None:
    assert yaml_pointer(["agents", "root", "skills", 2]) == "/agents/root/skills/2"


def test_yaml_pointer_escapes_reserved_characters() -> None:
    assert yaml_pointer(["a/b", "c~d"]) == "/a~1b/c~0d"


# ---- validate_discovered: happy path -------------------------------------


def test_validate_discovered_accepts_valid_fixtures() -> None:
    artifacts = discover(VALID / "full")
    errors = validate_discovered(artifacts)
    assert errors == []


def test_validate_discovered_accepts_minimal_fixture() -> None:
    artifacts = discover(VALID / "minimal")
    errors = validate_discovered(artifacts)
    assert errors == []


def test_validate_discovered_accepts_grouped_skills_fixture() -> None:
    artifacts = discover(VALID / "grouped-skills")
    errors = validate_discovered(artifacts)
    assert errors == []


# ---- validate_discovered: hand-crafted invalid inputs --------------------


def _artifact(kind: str, raw: dict[str, Any]) -> DiscoveredArtifact:
    return DiscoveredArtifact(path=Path(f"/fake/{kind}.yaml"), kind=kind, raw=raw)  # type: ignore[arg-type]


def test_validate_discovered_flags_required_field() -> None:
    # Topology missing its `agents.root` — the schema requires it.
    artifact = _artifact(
        "topology",
        {
            "apiVersion": "swael/v1",
            "kind": "Topology",
            "metadata": {"name": "broken", "version": "0.1.0"},
            "agents": {},
        },
    )
    (err,) = validate_discovered([artifact])
    assert err.code == "schema.required-field"
    assert err.yaml_pointer == "/agents"
    assert err.suggestion is not None
    assert "root" in err.suggestion


def test_validate_discovered_flags_enum_mismatch() -> None:
    artifact = _artifact(
        "topology",
        {
            "apiVersion": "swael/v1",
            "kind": "Topology",
            "metadata": {"name": "broken", "version": "0.1.0"},
            "agents": {
                "root": {"id": "root", "role": "supervisor"},  # not in the enum
            },
        },
    )
    errors = validate_discovered([artifact])
    # `supervisor` fails both the const-root rule and the child-role enum,
    # which is two errors under this schema. We just need at least one.
    codes = [e.code for e in errors]
    assert any(c in ("schema.enum-mismatch", "schema.const-mismatch") for c in codes)


def test_validate_discovered_flags_pattern_mismatch() -> None:
    artifact = _artifact(
        "skill",
        {
            "apiVersion": "swael/v1",
            "kind": "Skill",
            "metadata": {
                "id": "Bad_ID",  # uppercase + underscore — violates the id pattern
                "name": "Bad",
                "description": "x" * 12,
            },
            "category": "capability",
            "implementation": {"type": "mcp_tool", "server": "s", "tool": "t"},
            "provenance": {"authored_by": "human", "version": "0.1.0"},
        },
    )
    (err,) = validate_discovered([artifact])
    assert err.code == "schema.pattern-mismatch"
    assert err.yaml_pointer == "/metadata/id"
    assert err.suggestion is not None


def test_validate_discovered_aggregates_across_artifacts() -> None:
    # Three artifacts, each broken in a different way. All three errors
    # come back — no short-circuit.
    broken_topology = _artifact(
        "topology",
        {
            "apiVersion": "swael/v1",
            "kind": "Topology",
            "metadata": {"name": "x", "version": "0.1.0"},
            # agents.root missing entirely
            "agents": {},
        },
    )
    broken_skill = _artifact(
        "skill",
        {
            "apiVersion": "swael/v1",
            "kind": "Skill",
            "metadata": {
                "id": "bad-id",
                "name": "x",
                "description": "a" * 12,
            },
            # missing required `category`
            "implementation": {"type": "mcp_tool", "server": "s", "tool": "t"},
            "provenance": {"authored_by": "human", "version": "0.1.0"},
        },
    )
    broken_archetype = _artifact(
        "archetype",
        {
            "apiVersion": "swael/v1",
            "kind": "Archetype",
            "metadata": {"id": "x", "name": "x", "description": "a" * 12},
            # role missing entirely
            "defaults": {},
            "provenance": {"authored_by": "human", "version": "0.1.0"},
        },
    )
    errors = validate_discovered([broken_topology, broken_skill, broken_archetype])
    paths = {e.artifact_path.name for e in errors}
    assert {"topology.yaml", "skill.yaml", "archetype.yaml"}.issubset(paths)


# ---- resolution_error_from_discovery -------------------------------------


def test_bridges_workspace_not_found() -> None:
    exc = WorkspaceNotFoundError(Path("/nope"))
    err = resolution_error_from_discovery(exc)
    assert err.code == "workspace.not-found"
    assert err.artifact_path == Path("/nope")
    assert err.suggestion is not None


def test_bridges_missing_workspace_file() -> None:
    exc = MissingWorkspaceFileError(Path("/ws"))
    err = resolution_error_from_discovery(exc)
    assert err.code == "workspace.missing-workspace-yaml"
    assert "workspace.yaml" in (err.suggestion or "")


def test_bridges_yaml_parse_error() -> None:
    underlying = pyyaml.YAMLError("cosmetic")
    exc = YAMLParseError(Path("/ws/topologies/broken.yaml"), underlying)
    err = resolution_error_from_discovery(exc)
    assert err.code == "workspace.yaml-parse"
    assert err.artifact_path == Path("/ws/topologies/broken.yaml")


def test_bridges_kind_mismatch() -> None:
    exc = ArtifactKindMismatchError(Path("/ws/skills/wrong.yaml"), "Skill", "Topology")
    err = resolution_error_from_discovery(exc)
    assert err.code == "workspace.kind-mismatch"
    assert err.yaml_pointer == "/kind"
    assert err.suggestion is not None


def test_bridges_deep_nesting() -> None:
    exc = DeepNestingError(Path("/ws/skills/a/b/deep.yaml"))
    err = resolution_error_from_discovery(exc)
    assert err.code == "workspace.deep-nesting"


def test_bridges_malformed_artifact() -> None:
    exc = MalformedArtifactError(Path("/ws/skills/bad.yaml"), "list")
    err = resolution_error_from_discovery(exc)
    assert err.code == "workspace.malformed"


# ---- errors_or_raise -----------------------------------------------------


def test_errors_or_raise_noop_when_empty() -> None:
    errors_or_raise([], [])  # no raise


def test_errors_or_raise_raises_combined() -> None:
    a = ResolutionError(code="x", message="m1", artifact_path=Path("/a"))
    b = ResolutionError(code="y", message="m2", artifact_path=Path("/b"))
    with pytest.raises(ResolutionErrors) as excinfo:
        errors_or_raise([a], [b])
    assert list(excinfo.value) == [a, b]
