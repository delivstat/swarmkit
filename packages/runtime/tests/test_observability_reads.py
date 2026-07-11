"""GET /observability/runs/{id}/trace + GET /audit — the monitor's trace-waterfall + audit reads
(design: details/workspace-ui.md, slice 2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime._workspace_runtime import RunResult
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.server import create_app
from swarmkit_runtime.server._jobs import Job, execute_job
from swarmkit_runtime.server._routes_introspection import (
    _audit_event_to_dict,
    _span_to_dict,
)
from swarmkit_runtime.telemetry import RecordedSpan
from swarmkit_runtime.trace import AgentStep, RunTrace, ToolCall

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


def test_span_to_dict_serializes_the_tree_with_durations() -> None:
    child = RecordedSpan(
        name="tool.call.say-hello", start_ns=1_000_000, end_ns=3_000_000, attributes={"n": 1}
    )
    root = RecordedSpan(
        name="topology.run",
        start_ns=0,
        end_ns=5_000_000,
        attributes={"swarmkit.run.id": "r1"},
        children=(child,),
    )
    d = _span_to_dict(root)
    assert d["name"] == "topology.run"
    assert d["duration_ms"] == 5.0
    assert d["attributes"]["swarmkit.run.id"] == "r1"
    assert len(d["children"]) == 1
    assert d["children"][0]["name"] == "tool.call.say-hello"
    assert d["children"][0]["duration_ms"] == 2.0


def test_audit_event_to_dict_serializes_uuid_and_datetime() -> None:
    eid = uuid4()
    ts = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    event = AuditEvent(
        event_type="policy.denied",
        agent_id="a1",
        timestamp=ts,
        payload={"k": "v"},
        event_id=eid,
        run_id="r1",
    )
    d = _audit_event_to_dict(event)
    assert d["event_id"] == str(eid)
    assert d["event_type"] == "policy.denied"
    assert d["timestamp"] == ts.isoformat()
    assert d["run_id"] == "r1"
    assert d["payload"] == {"k": "v"}


def test_trace_endpoint_404_for_unknown_run() -> None:
    with TestClient(create_app(EXAMPLE_WS)) as client:
        assert client.get("/observability/runs/does-not-exist/trace").status_code == 404


def test_audit_endpoint_returns_a_list() -> None:
    with TestClient(create_app(EXAMPLE_WS)) as client:
        res = client.get("/audit")
        assert res.status_code == 200
        assert isinstance(res.json(), list)


def test_trace_endpoint_serves_a_persisted_run_trace() -> None:
    trace = RunTrace(
        run_id="ui-test-run", topology="hello", start_time=1.0, end_time=2.0, duration_ms=1000
    )
    trace.agent_steps.append(
        AgentStep(
            agent_id="greeter",
            start_time=1.0,
            end_time=2.0,
            duration_ms=1000,
            tool_calls=[ToolCall(tool_name="say-hello", duration_ms=100)],
        )
    )
    trace.save(EXAMPLE_WS)  # writes .swarmkit/traces/ui-test-run.json (gitignored)
    try:
        with TestClient(create_app(EXAMPLE_WS)) as client:
            res = client.get("/observability/runs/ui-test-run/trace")
            assert res.status_code == 200
            tree = res.json()
            assert tree["name"] == "topology.run"
            assert tree["attributes"]["swarmkit.run.id"] == "ui-test-run"
            assert len(tree["children"]) == 1
            step = tree["children"][0]
            assert step["name"].startswith("agent.step")
            assert len(step["children"]) == 1  # the tool call
            assert step["children"][0]["name"].startswith("tool.call")
    finally:
        (EXAMPLE_WS / ".swarmkit" / "traces" / "ui-test-run.json").unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_execute_job_keys_the_run_by_job_id() -> None:
    """execute_job passes thread_id=job.id to rt.run, so the run's trace is keyed by the job id and
    the run-detail UI can fetch GET /observability/runs/{job_id}/trace directly — no separate
    job→run_id mapping. (An end-to-end run can't be tested here — it needs a live model.)"""
    job = Job(id="job-xyz", topology="hello", status="pending", input="hi")
    rt = MagicMock()
    rt.run = AsyncMock(return_value=RunResult(output="ok"))

    await execute_job(job, rt, max_steps=5)

    rt.run.assert_awaited_once()
    assert rt.run.await_args.kwargs["thread_id"] == "job-xyz"
