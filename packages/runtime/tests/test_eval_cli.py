"""`swarmkit eval` CLI wiring test (mock provider, copied workspace)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import copy_workspace
from swarmkit_runtime.cli import app
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


def _runs(ws: Path) -> list[dict[str, object]]:
    result = runner.invoke(app, ["eval-runs", "list", str(ws), "--json"])
    assert result.exit_code == 0, result.output
    runs: list[dict[str, object]] = json.loads(result.stdout)
    return runs


def test_eval_runs_loads_and_records_the_run(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    copy_workspace(EXAMPLE_WS, ws)
    result = runner.invoke(app, ["eval", str(ws), "greeting-evals"])
    # exit 0 (all pass) or 1 (some fail) — both are valid "ran" outcomes; not a usage error
    assert result.exit_code in (0, 1), result.stdout
    assert "passed" in result.stdout
    [run] = _runs(ws)
    assert run["eval_set_id"] == "greeting-evals" and run["source"] == "cli"
    assert run["status"] in ("succeeded", "failed") and run["recorded"] == 2
    assert f"eval run: {run['id']}" in result.stdout
    # The store is the record now; nothing is written under eval-results/ any more.
    assert not (ws / ".swarmkit" / "eval-results").exists()


def test_output_still_writes_the_json_report(tmp_path: Path) -> None:
    """CI reads `--output`, so its shape stays the report it always was."""
    ws = tmp_path / "ws"
    copy_workspace(EXAMPLE_WS, ws)
    out = tmp_path / "report.json"
    runner.invoke(app, ["eval", str(ws), "greeting-evals", "-o", str(out)])
    report = json.loads(out.read_text())
    assert report["eval_set_id"] == "greeting-evals"
    assert report["total"] == 2 and len(report["cases"]) == 2
    assert {"pass_rate", "passed", "failed", "started_at", "finished_at"} <= set(report)


def test_compare_reads_the_previous_run_from_the_store(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    copy_workspace(EXAMPLE_WS, ws)
    first = runner.invoke(app, ["eval", str(ws), "greeting-evals", "--compare"])
    assert "compare: no prior run" in first.stdout
    second = runner.invoke(app, ["eval", str(ws), "greeting-evals", "--compare"])
    assert "compare: pass rate" in second.stdout


def test_show_and_a_refused_resume(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    copy_workspace(EXAMPLE_WS, ws)
    runner.invoke(app, ["eval", str(ws), "greeting-evals"])
    [run] = _runs(ws)
    shown = runner.invoke(app, ["eval-runs", "show", str(ws), str(run["id"])])
    assert shown.exit_code == 0, shown.output
    assert "greets-and-not-empty" in shown.stdout and "addresses-the-audience" in shown.stdout
    resumed = runner.invoke(app, ["eval-runs", "resume", str(ws), str(run["id"])])
    assert resumed.exit_code == 2
    assert "only an interrupted or stopped run can be resumed" in resumed.output
    stopped = runner.invoke(app, ["eval-runs", "stop", str(ws), str(run["id"])])
    assert stopped.exit_code == 2 and "is not running" in stopped.output
    missing = runner.invoke(app, ["eval-runs", "show", str(ws), "no-such-run"])
    assert missing.exit_code == 2


def test_list_with_nothing_recorded(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    copy_workspace(EXAMPLE_WS, ws)
    result = runner.invoke(app, ["eval-runs", "list", str(ws)])
    assert result.exit_code == 0 and "No eval runs recorded yet" in result.stdout


def test_eval_unknown_set_is_usage_error(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    copy_workspace(EXAMPLE_WS, ws)
    result = runner.invoke(app, ["eval", str(ws), "does-not-exist"])
    assert result.exit_code == 2  # usage error
