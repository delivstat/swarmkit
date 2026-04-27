"""Tests for ``swarmkit run`` CLI command (M3).

See ``design/details/langgraph-compiler.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swael_runtime.cli import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force mock provider in all run tests so they don't hit real APIs."""
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
BROKEN_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace-broken"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "workspaces"
RESOLVED_TREE_WS = FIXTURES / "resolved-tree"


def test_run_valid_workspace_exits_zero() -> None:
    result = runner.invoke(
        app, ["run", str(EXAMPLE_WS), "hello", "--input", "Greet engineers", "--no-color"]
    )
    assert result.exit_code == 0
    assert result.stdout.strip() != ""


def test_run_prints_output() -> None:
    result = runner.invoke(app, ["run", str(EXAMPLE_WS), "hello", "--input", "Hi", "--no-color"])
    assert result.exit_code == 0
    assert "mock response" in result.stdout.lower() or len(result.stdout.strip()) > 0


def test_run_invalid_workspace_exits_one() -> None:
    result = runner.invoke(app, ["run", str(BROKEN_WS), "hello", "--no-color"])
    assert result.exit_code == 1
    combined = (result.stdout or "") + (result.stderr or "")
    assert "error" in combined.lower()


def test_run_missing_topology_exits_two() -> None:
    result = runner.invoke(app, ["run", str(EXAMPLE_WS), "nonexistent", "--no-color"])
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "not found" in combined.lower()


def test_run_nonexistent_workspace_exits_one() -> None:
    result = runner.invoke(app, ["run", "/nowhere/nothing", "hello", "--no-color"])
    assert result.exit_code != 0


def test_run_rejects_skill_referencing_unconfigured_mcp_server() -> None:
    """The resolved-tree fixture has mcp_tool skills but no mcp_servers
    block. The CLI must catch this at compile time and tell the user
    which skill and which server, instead of letting the topology run
    and produce a cryptic per-call error.
    """
    result = runner.invoke(
        app, ["run", str(RESOLVED_TREE_WS), "review", "--input", "x", "--no-color"]
    )
    assert result.exit_code == 1
    combined = (result.stdout or "") + (result.stderr or "")
    assert "mcp_servers" in combined
    assert "rynko-flow" in combined or "github-repo" in combined
