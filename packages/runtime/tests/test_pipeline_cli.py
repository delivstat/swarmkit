"""`swarmkit pipeline` CLI (design/details/bundled-pipeline-orchestrator.md §4): emit enqueues an
event to the durable store; sagas/status read run state; advance/skip enqueue operator gate events —
over the same store the orchestrator + serve use."""

from __future__ import annotations

import json
from pathlib import Path

from swarmkit_runtime.cli import app as cli_app
from swarmkit_runtime.orchestration import SqlSagaStore
from typer.testing import CliRunner

_runner = CliRunner()


def _db(ws: Path) -> str:
    return f"sqlite:///{ws / '.swarmkit' / 'store.sqlite'}"


def test_emit_enqueues_a_start_event(tmp_path: Path) -> None:
    r = _runner.invoke(
        cli_app,
        [
            "pipeline",
            "emit",
            "sterling-dev",
            "-w",
            str(tmp_path),
            "--tag",
            "site-42",
            "--correlation",
            "run-1",
        ],
    )
    assert r.exit_code == 0 and "run-1" in r.stdout

    store = SqlSagaStore.from_url(_db(tmp_path))
    claimed = store.claim("w")
    assert claimed is not None
    _id, cid, event = claimed
    assert cid == "run-1"
    data = json.loads(event)
    assert data == {"kind": "start", "graph": "sterling-dev", "tag": "site-42", "input": ""}


def test_sagas_and_status_read_runs(tmp_path: Path) -> None:
    store = SqlSagaStore.from_url(_db(tmp_path))
    s = store.create("run-a", graph_id="sterling-dev", tag="site-42")
    s.passed_stages = ["locate"]
    s.status = "parked"
    s.pending_gate_stage = "build"
    store.save(s)

    listed = _runner.invoke(cli_app, ["pipeline", "sagas", "-w", str(tmp_path), "--json"])
    rows = json.loads(listed.stdout)
    assert rows[0]["correlation_id"] == "run-a" and rows[0]["status"] == "parked"

    # search by correlation id
    found = _runner.invoke(cli_app, ["pipeline", "sagas", "run-a", "-w", str(tmp_path), "--json"])
    assert json.loads(found.stdout)[0]["correlation_id"] == "run-a"

    detail = _runner.invoke(cli_app, ["pipeline", "status", "run-a", "-w", str(tmp_path), "--json"])
    assert json.loads(detail.stdout)["pending_gate_stage"] == "build"


def test_advance_enqueues_gate_event(tmp_path: Path) -> None:
    _runner.invoke(cli_app, ["pipeline", "advance", "run-a", "build", "-w", str(tmp_path)])
    store = SqlSagaStore.from_url(_db(tmp_path))
    claimed = store.claim("w")
    assert claimed is not None
    assert json.loads(claimed[2]) == {"kind": "gate", "approved": True, "stage": "build"}
