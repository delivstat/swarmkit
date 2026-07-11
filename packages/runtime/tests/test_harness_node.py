"""Harness node execution: sandbox → run → budget → artifact → teardown (executor P2, PR6)."""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from swarmkit_runtime.executors import (
    BudgetEnvelope,
    ExecApprovalRequested,
    ExecEvent,
    ExecMessage,
    ExecResult,
    ExecStarted,
    ExecUsage,
    Executor,
    PreflightReport,
    ResolvedExecutor,
    SandboxHandle,
    TaskSpec,
)
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._harness_node import run_harness_node
from swarmkit_runtime.langgraph_compiler._state import SwarmState
from swarmkit_runtime.resolver import ResolvedAgent


def _git_workspace(root: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "t@t.test")
    git("config", "user.name", "t")
    (root / "CLAUDE.md").write_text("# workspace rules\n")
    git("add", "-A")
    git("commit", "-m", "seed")


def _agent(kind: str = "fake") -> ResolvedAgent:
    return ResolvedAgent(
        id="root",
        role="root",
        model=None,
        prompt=None,
        skills=(),
        iam=None,
        executor=ResolvedExecutor(kind=kind),
    )


def _state() -> SwarmState:
    # run_harness_node only reads `input`; a partial state is fine at runtime.
    return cast(SwarmState, {"input": "add a file", "agent_results": {}, "output": ""})


class _FakeHarness(Executor):
    """A harness that writes a file into the sandbox and reports success — no real subprocess."""

    kind = "fake"

    def __init__(self, events_factory: Any) -> None:
        self._events_factory = events_factory
        self.cancelled: list[str] = []

    def config_schema(self) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": True}

    def preflight(self, task: TaskSpec, sandbox: SandboxHandle) -> PreflightReport:
        return PreflightReport(ok=True)

    async def run(
        self, task: TaskSpec, sandbox: SandboxHandle, budget: BudgetEnvelope
    ) -> AsyncIterator[ExecEvent]:
        async for event in self._events_factory(sandbox):
            yield event

    async def cancel(self, run_id: str) -> None:
        self.cancelled.append(run_id)


@pytest.mark.asyncio
async def test_success_run_collects_diff_and_records_audit(tmp_path: Path) -> None:
    _git_workspace(tmp_path)

    async def events(sandbox: SandboxHandle) -> AsyncIterator[ExecEvent]:
        yield ExecStarted(run_id="r1", kind="fake")
        (sandbox.root / "generated.txt").write_text("hello from harness\n")
        yield ExecMessage(role="assistant", text="wrote the file")
        yield ExecUsage(cost_usd=0.02)
        yield ExecResult(status="success", output="Created generated.txt")

    gov = MockGovernanceProvider()
    runner = _FakeHarness(events)
    result = await run_harness_node(
        _agent(), _state(), gov, workspace_root=tmp_path, executor=runner
    )

    assert "Created generated.txt" in result["output"]
    assert "diff" in result["output"]  # artifact note
    types = [e.event_type for e in gov.events]
    assert "executor.started" in types and "executor.result" in types
    res_event = next(e for e in gov.events if e.event_type == "executor.result")
    assert res_event.payload["status"] == "success"
    assert res_event.payload["cost_usd"] == pytest.approx(0.02)
    assert cast(int, res_event.payload["diff_bytes"]) > 0
    # the sandbox (a temp worktree) is torn down — no leftover checkout under the repo.
    assert not (tmp_path / "generated.txt").exists()


@pytest.mark.asyncio
async def test_approval_request_denies_and_never_hangs(tmp_path: Path) -> None:
    _git_workspace(tmp_path)

    async def events(sandbox: SandboxHandle) -> AsyncIterator[ExecEvent]:
        yield ExecStarted(run_id="r1", kind="fake")
        yield ExecApprovalRequested(run_id="r1", capability="network:egress")
        # a compliant adapter stops here; if it kept going we'd still have terminated.
        yield ExecResult(status="success", output="should be ignored")

    gov = MockGovernanceProvider()
    runner = _FakeHarness(events)
    result = await run_harness_node(
        _agent(), _state(), gov, workspace_root=tmp_path, executor=runner
    )

    assert "needs_approval" in result["output"]
    assert runner.cancelled == ["r1"]  # the run was aborted
    res_event = next(e for e in gov.events if e.event_type == "executor.result")
    assert res_event.payload["status"] == "needs_approval"


@pytest.mark.asyncio
async def test_preflight_failure_returns_result_without_sandbox(tmp_path: Path) -> None:
    _git_workspace(tmp_path)

    class _BadPreflight(_FakeHarness):
        def preflight(self, task: TaskSpec, sandbox: SandboxHandle) -> PreflightReport:
            return PreflightReport(ok=False, reason="binary missing")

    async def events(sandbox: SandboxHandle) -> AsyncIterator[ExecEvent]:
        yield ExecResult(status="success", output="x")

    gov = MockGovernanceProvider()
    result = await run_harness_node(
        _agent(), _state(), gov, workspace_root=tmp_path, executor=_BadPreflight(events)
    )
    assert "preflight failed" in result["output"]
    assert any(e.event_type == "executor.failed" for e in gov.events)


@pytest.mark.asyncio
async def test_no_workspace_root_fails_gracefully() -> None:
    gov = MockGovernanceProvider()
    result = await run_harness_node(
        _agent(), _state(), gov, workspace_root=None, executor=_FakeHarness(None)
    )
    assert "no workspace root" in result["output"]


@pytest.mark.asyncio
async def test_unrunnable_kind_without_injected_executor(tmp_path: Path) -> None:
    _git_workspace(tmp_path)
    agent = dataclasses.replace(_agent(), executor=ResolvedExecutor(kind="mystery"))
    gov = MockGovernanceProvider()
    result = await run_harness_node(agent, _state(), gov, workspace_root=tmp_path)
    assert "no runnable adapter" in result["output"]
