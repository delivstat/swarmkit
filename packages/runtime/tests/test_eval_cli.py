"""`swarmkit eval` CLI wiring test (mock provider, copied workspace)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from swarmkit_runtime.cli import app
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


def test_eval_runs_loads_and_writes_report(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    shutil.copytree(EXAMPLE_WS, ws)
    result = runner.invoke(app, ["eval", str(ws), "greeting-evals"])
    # exit 0 (all pass) or 1 (some fail) — both are valid "ran" outcomes; not a usage error
    assert result.exit_code in (0, 1), result.stdout
    assert "passed" in result.stdout
    reports = list((ws / ".swarmkit" / "eval-results").glob("greeting-evals-*.json"))
    assert reports, "no eval report was written"


def test_eval_unknown_set_is_usage_error(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    shutil.copytree(EXAMPLE_WS, ws)
    result = runner.invoke(app, ["eval", str(ws), "does-not-exist"])
    assert result.exit_code == 2  # usage error
