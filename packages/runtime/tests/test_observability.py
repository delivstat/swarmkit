"""Unit tests for the Observability facade — the `.swarmkit` layout + audit/JSONL read logic.

These paths were copy-pasted across the logs/status/why/ask CLI commands and had no coverage;
this exercises the facade directly.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from swarmkit_runtime._observability import Observability
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.governance import AuditEvent


def _write_jsonl(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


# ---- .swarmkit layout -------------------------------------------------------


def test_layout_paths(tmp_path: Path) -> None:
    obs = Observability(tmp_path)
    sk = tmp_path / ".swarmkit"
    assert obs.swarmkit_dir == sk
    assert obs.audit_db == sk / "audit.sqlite"
    assert obs.logs_dir == sk / "logs"
    assert obs.traces_dir == sk / "traces"
    assert obs.prompts_db == sk / "prompts.sqlite"
    assert obs.tasks_json == sk / "run-state" / "current" / "tasks.json"
    assert obs.last_thread_file == sk / "state" / "last_thread.txt"
    assert obs.checkpoints_db == sk / "state" / "checkpoints.db"


# ---- JSONL run logs ---------------------------------------------------------


def test_run_log_files_newest_first_with_filter_and_limit(tmp_path: Path) -> None:
    logs = tmp_path / ".swarmkit" / "logs"
    for name in ("hello-1.jsonl", "hello-2.jsonl", "other-1.jsonl"):
        _write_jsonl(logs / name, [{"event_type": "agent.started"}])
    obs = Observability(tmp_path)

    files = obs.run_log_files()
    assert [f.name for f in files] == ["other-1.jsonl", "hello-2.jsonl", "hello-1.jsonl"]  # newest

    assert [f.name for f in obs.run_log_files(topology="hello")] == [
        "hello-2.jsonl",
        "hello-1.jsonl",
    ]
    assert [f.name for f in obs.run_log_files(limit=1)] == ["other-1.jsonl"]


def test_run_log_files_missing_dir_is_empty(tmp_path: Path) -> None:
    assert Observability(tmp_path).run_log_files() == []


def test_read_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    f = tmp_path / "run.jsonl"
    f.write_text('{"a": 1}\n\n{"b": 2}\n', encoding="utf-8")
    assert Observability(tmp_path).read_jsonl(f) == [{"a": 1}, {"b": 2}]


def test_find_run_log_by_prefix(tmp_path: Path) -> None:
    logs = tmp_path / ".swarmkit" / "logs"
    _write_jsonl(logs / "hello-1.jsonl", [{"x": 1}])
    _write_jsonl(logs / "hello-2.jsonl", [{"x": 2}])
    obs = Observability(tmp_path)
    assert obs.find_run_log("hello").name == "hello-2.jsonl"  # newest match
    assert obs.find_run_log("nope") is None


# ---- traces -----------------------------------------------------------------


def test_find_trace(tmp_path: Path) -> None:
    traces = tmp_path / ".swarmkit" / "traces"
    traces.mkdir(parents=True)
    (traces / "run-abc-123.json").write_text("{}", encoding="utf-8")
    obs = Observability(tmp_path)
    assert obs.find_trace("run-abc").name == "run-abc-123.json"
    assert obs.find_trace("missing") is None
    assert Observability(tmp_path / "empty").find_trace("x") is None  # no traces dir


# ---- audit store ------------------------------------------------------------


def _record(ws: Path, event: AuditEvent) -> None:
    provider = WorkspaceRuntime.audit_provider_for(ws)
    try:
        asyncio.run(provider.record(event))
    finally:
        provider.close_sync()


def test_query_audit_none_when_no_store(tmp_path: Path) -> None:
    assert Observability(tmp_path).query_audit(limit=10) is None


def test_query_audit_none_when_store_empty(tmp_path: Path) -> None:
    # Opening the provider creates the sqlite file but records nothing → count 0 → None (fall back).
    WorkspaceRuntime.audit_provider_for(tmp_path).close_sync()
    assert Observability(tmp_path).audit_db.is_file()
    assert Observability(tmp_path).query_audit(limit=10) is None


def test_query_audit_returns_recorded_events(tmp_path: Path) -> None:
    _record(
        tmp_path,
        AuditEvent(
            event_type="agent.completed",
            agent_id="root",
            timestamp=datetime.now(UTC),
            run_id="run-1",
            topology_id="hello",
            duration_ms=42,
        ),
    )
    events = Observability(tmp_path).query_audit(limit=10)
    assert events is not None and len(events) == 1
    assert events[0].agent_id == "root" and events[0].duration_ms == 42


def test_filter_by_run(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    a = AuditEvent(event_type="x", agent_id="a", timestamp=now, run_id="run-abc", topology_id="hi")
    b = AuditEvent(event_type="x", agent_id="b", timestamp=now, run_id="run-xyz", topology_id="bye")
    events = [a, b]
    assert Observability.filter_by_run(events, None) == events  # empty filter → all
    assert Observability.filter_by_run(events, "abc") == [a]  # matches run_id
    assert Observability.filter_by_run(events, "bye") == [b]  # matches topology_id
    assert Observability.filter_by_run(events, "none") == []


def test_workspace_runtime_factory(tmp_path: Path) -> None:
    obs = WorkspaceRuntime.observability(tmp_path)
    assert isinstance(obs, Observability)
    assert obs.swarmkit_dir == tmp_path / ".swarmkit"
