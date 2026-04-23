"""Tests for ``swarmkit run`` CLI command (M3).

See ``design/details/langgraph-compiler.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.cli import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force mock provider in all run tests so they don't hit real APIs."""
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
BROKEN_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace-broken"


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
