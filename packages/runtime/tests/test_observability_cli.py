"""End-to-end tests for the observability CLI commands (logs/status/why/trace/checkpoints/debug).

These commands had no coverage before the service-layer extraction. Each drives the real Typer
app via CliRunner against a throwaway workspace with fabricated `.swarmkit/` state.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.cli import app
from swarmkit_runtime.governance import AuditEvent
from typer.testing import CliRunner

runner = CliRunner()

_EVENTS = [
    {"event_type": "agent.started", "agent_id": "root", "role": "root", "timestamp": "2026-01-01"},
    {"event_type": "agent.completed", "agent_id": "root", "duration_ms": 120, "timestamp": "t"},
    {"event_type": "skill.executed", "agent_id": "root", "skill_id": "greet", "timestamp": "t"},
]


def _ws_with_jsonl(tmp_path: Path, name: str = "hello-1700000000.jsonl") -> Path:
    logs = tmp_path / ".swarmkit" / "logs"
    logs.mkdir(parents=True)
    (logs / name).write_text("\n".join(json.dumps(e) for e in _EVENTS) + "\n", encoding="utf-8")
    return tmp_path


# ---- logs -------------------------------------------------------------------


def test_logs_jsonl_text(tmp_path: Path) -> None:
    ws = _ws_with_jsonl(tmp_path)
    result = runner.invoke(app, ["logs", str(ws)])
    assert result.exit_code == 0, result.output
    assert "hello-1700000000.jsonl" in result.output
    assert "root" in result.output and "skill" in result.output


def test_logs_markdown(tmp_path: Path) -> None:
    ws = _ws_with_jsonl(tmp_path)
    result = runner.invoke(app, ["logs", str(ws), "--format", "markdown"])
    assert result.exit_code == 0
    assert "# Run Report:" in result.output and "| Agents completed | 1 |" in result.output


def test_logs_topology_filter_no_match(tmp_path: Path) -> None:
    ws = _ws_with_jsonl(tmp_path)
    result = runner.invoke(app, ["logs", str(ws), "--topology", "nope"])
    assert result.exit_code == 0 and "No matching run logs" in result.output


def test_logs_no_logs_dir(tmp_path: Path) -> None:
    result = runner.invoke(app, ["logs", str(tmp_path)])
    assert result.exit_code == 0 and "No run logs found" in result.output


def test_logs_from_audit_store(tmp_path: Path) -> None:
    provider = WorkspaceRuntime.audit_provider_for(tmp_path)
    asyncio.run(
        provider.record(
            AuditEvent(
                event_type="agent.completed",
                agent_id="root",
                timestamp=datetime.now(UTC),
                duration_ms=99,
                run_id="r1",
                topology_id="hello",
            )
        )
    )
    provider.close_sync()
    result = runner.invoke(app, ["logs", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "audit store" in result.output and "root" in result.output


# ---- status -----------------------------------------------------------------


def test_status_jsonl(tmp_path: Path) -> None:
    ws = _ws_with_jsonl(tmp_path)
    result = runner.invoke(app, ["status", str(ws)])
    assert result.exit_code == 0
    assert "topology" in result.output and "hello" in result.output


def test_status_no_runs(tmp_path: Path) -> None:
    result = runner.invoke(app, ["status", str(tmp_path)])
    assert result.exit_code == 0 and "No runs recorded yet" in result.output


# ---- why (data-gathering / error path, no LLM) ------------------------------


def test_why_no_events_exits_usage(tmp_path: Path) -> None:
    result = runner.invoke(app, ["why", "ghost", str(tmp_path)])
    assert result.exit_code == 2
    assert "No events found" in result.output


# ---- trace ------------------------------------------------------------------


def test_trace_no_traces(tmp_path: Path) -> None:
    result = runner.invoke(app, ["trace", "--workspace", str(tmp_path)])
    assert result.exit_code == 0 and "No traces found" in result.output


def test_trace_run_not_found(tmp_path: Path) -> None:
    result = runner.invoke(app, ["trace", "run-x", "--workspace", str(tmp_path)])
    assert result.exit_code == 1 and "Trace not found" in result.output


# ---- checkpoints ------------------------------------------------------------


def test_checkpoints_none(tmp_path: Path) -> None:
    result = runner.invoke(app, ["checkpoints", "--workspace", str(tmp_path)])
    assert result.exit_code == 0 and "No checkpointed runs found" in result.output


def test_checkpoints_with_thread(tmp_path: Path) -> None:
    state = tmp_path / ".swarmkit" / "state"
    state.mkdir(parents=True)
    (state / "last_thread.txt").write_text("thread-xyz\n", encoding="utf-8")
    result = runner.invoke(app, ["checkpoints", "--workspace", str(tmp_path)])
    assert result.exit_code == 0
    assert "Last checkpointed run: thread-xyz" in result.output
    assert "no checkpoint database found" in result.output


# ---- debug ------------------------------------------------------------------


def test_debug_no_ring_buffer(tmp_path: Path) -> None:
    result = runner.invoke(app, ["debug", str(tmp_path)])
    assert result.exit_code == 0 and "No prompt ring buffer found" in result.output
