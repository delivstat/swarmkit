"""Tests for the ``swarmkit validate`` CLI (M1.6) and the error renderer (task #23)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from swarmkit_runtime.cli import app
from swarmkit_runtime.cli._render import (
    render_error,
    render_errors,
    render_success,
    should_colour,
)
from swarmkit_runtime.errors import ResolutionError
from swarmkit_runtime.resolver import resolve_workspace
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VALID = FIXTURES / "workspaces"
INVALID = FIXTURES / "workspaces-invalid"

runner = CliRunner()


# ---- renderer unit tests ----------------------------------------------


def test_render_error_includes_all_fields() -> None:
    err = ResolutionError(
        code="agent.unknown-archetype",
        message="Agent 'root' references archetype 'missing' which is not defined.",
        artifact_path=Path("/ws/topologies/bad.yaml"),
        yaml_pointer="/agents/root/archetype",
        rule="archetype.unknown-skill",
        suggestion="Define an archetype with id='missing' or drop the reference.",
    )
    out = render_error(err, color=False)
    assert "error:" in out
    assert "Agent 'root'" in out
    assert "/agents/root/archetype" in out
    assert "archetype.unknown-skill" in out
    assert "Define an archetype" in out


def test_render_error_workspace_relative_path() -> None:
    err = ResolutionError(
        code="x",
        message="y",
        artifact_path=Path("/ws/skills/foo.yaml"),
    )
    out = render_error(err, workspace_root=Path("/ws"), color=False)
    assert "skills/foo.yaml" in out
    assert "/ws/skills/foo.yaml" not in out


def test_render_error_falls_back_to_code_when_no_rule() -> None:
    err = ResolutionError(code="skill.duplicate-id", message="m", artifact_path=Path("/p"))
    out = render_error(err, color=False)
    assert "skill.duplicate-id" in out


def test_render_error_color_toggles_ansi() -> None:
    err = ResolutionError(code="x", message="m", artifact_path=Path("/p"))
    plain = render_error(err, color=False)
    coloured = render_error(err, color=True)
    assert "\x1b[" in coloured
    assert "\x1b[" not in plain


def test_render_errors_summary_respects_plurality() -> None:
    one = ResolutionError(code="a", message="m", artifact_path=Path("/a"))
    two = ResolutionError(code="b", message="m", artifact_path=Path("/a"))
    out = render_errors([one, two], color=False)
    # 2 errors across 1 file — plurality correct on both.
    assert "2 errors across 1 file." in out


def test_render_errors_multi_file_plural() -> None:
    errs = [
        ResolutionError(code="a", message="m", artifact_path=Path("/a")),
        ResolutionError(code="b", message="m", artifact_path=Path("/b")),
    ]
    out = render_errors(errs, color=False)
    assert "2 errors across 2 files." in out


# ---- should_colour ----------------------------------------------------


def test_should_colour_honours_override() -> None:
    assert should_colour(stream_is_tty=False, color_override=True) is True
    assert should_colour(stream_is_tty=True, color_override=False) is False


def test_should_colour_honours_no_color_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert should_colour(stream_is_tty=True, color_override=None) is False


def test_should_colour_defaults_to_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert should_colour(stream_is_tty=True, color_override=None) is True
    assert should_colour(stream_is_tty=False, color_override=None) is False


# ---- success renderer --------------------------------------------------


def test_render_success_lists_registries() -> None:
    workspace = resolve_workspace(VALID / "full")
    out = render_success(workspace, tree=False, color=False)
    assert "full-workspace" in out
    assert "topologies: 1" in out
    assert "hello" in out
    assert "no errors" in out


def test_render_success_tree_mode_expands_agent_tree() -> None:
    workspace = resolve_workspace(VALID / "resolved-tree")
    out = render_success(workspace, tree=True, color=False)
    assert "topology: review" in out
    assert "root (role=root, archetype=supervisor-root)" in out
    assert "reviewer (role=worker, archetype=code-review-worker)" in out
    assert "independent-worker (role=worker)" in out


# ---- CLI integration --------------------------------------------------


def test_validate_ok_exits_zero() -> None:
    result = runner.invoke(app, ["validate", str(VALID / "full"), "--no-color"])
    assert result.exit_code == 0
    assert "full-workspace" in result.stdout
    assert "no errors" in result.stdout


def test_validate_ok_quiet_suppresses_summary() -> None:
    result = runner.invoke(app, ["validate", str(VALID / "full"), "--quiet", "--no-color"])
    assert result.exit_code == 0
    assert result.stdout == ""


def test_validate_invalid_exits_one() -> None:
    result = runner.invoke(app, ["validate", str(INVALID / "abstract-ambiguous"), "--no-color"])
    assert result.exit_code == 1
    assert "error:" in result.stdout
    assert "content-review-strict" in result.stdout
    assert "content-review-lenient" in result.stdout


def test_validate_json_ok() -> None:
    result = runner.invoke(app, ["validate", str(VALID / "full"), "--json"])
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    obj = json.loads(lines[0])
    assert obj["event"] == "validate.ok"
    assert obj["workspace"] == "full-workspace"
    assert obj["topologies"] == 1
    assert obj["triggers"] == 2


def test_validate_json_error() -> None:
    result = runner.invoke(app, ["validate", str(INVALID / "abstract-ambiguous"), "--json"])
    assert result.exit_code == 1
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    events = [json.loads(ln) for ln in lines]
    # One error event + one summary event.
    assert events[-1]["event"] == "validate.summary"
    assert events[-1]["status"] == "failed"
    assert events[-1]["errors"] == 1
    assert any(e.get("event") == "validate.error" for e in events)


def test_validate_json_tree_emits_topology_objects() -> None:
    result = runner.invoke(app, ["validate", str(VALID / "resolved-tree"), "--json", "--tree"])
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    events = [json.loads(ln) for ln in lines]
    kinds = [e["event"] for e in events]
    assert "validate.ok" in kinds
    assert "validate.topology" in kinds
    topology_event = next(e for e in events if e["event"] == "validate.topology")
    assert topology_event["id"] == "review"
    assert topology_event["root"]["id"] == "root"
    assert topology_event["root"]["archetype"] == "supervisor-root"


def test_validate_nonexistent_path_exits_one() -> None:
    # DiscoveryError -> ResolutionErrors (exit 1); not exit 2 which is
    # reserved for CLI usage errors. A nonexistent path is a "workspace
    # not found" resolution error with a useful suggestion.
    result = runner.invoke(app, ["validate", "/nowhere/nothing", "--no-color"])
    assert result.exit_code == 1
    assert "error:" in result.stdout
    assert "not found" in result.stdout.lower()


def test_validate_tree_output_shows_agent_details() -> None:
    result = runner.invoke(app, ["validate", str(VALID / "resolved-tree"), "--tree", "--no-color"])
    assert result.exit_code == 0
    assert "topology: review" in result.stdout
    assert "archetype=supervisor-root" in result.stdout
