"""The relay orchestrator + harness-node relay wiring (executor-relay-plan.md, relay PR2)."""

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
    Executor,
    InteractionDriver,
    PreflightReport,
    ResolvedExecutor,
    SandboxHandle,
    TaskSpec,
    approve_launch,
    load_workspace_adapter_specs,
)
from swarmkit_runtime.governance import PolicyDecision
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._harness_node import run_harness_node
from swarmkit_runtime.langgraph_compiler._relay import resolve_relay
from swarmkit_runtime.langgraph_compiler._state import SwarmState
from swarmkit_runtime.resolver import ResolvedAgent
from swarmkit_runtime.review import ReviewItem, ReviewQueue


class _MemQueue:
    """In-memory ReviewQueue; a preset decision resolves the item immediately (no real waiting)."""

    def __init__(self, preset: str | None = None) -> None:
        self._items: dict[str, ReviewItem] = {}
        self._preset = preset  # "approved" | "rejected" | None

    def submit(self, item: ReviewItem) -> None:
        if self._preset is not None:
            item = dataclasses.replace(item, status=self._preset)  # type: ignore[arg-type]
        self._items[item.id] = item

    def list_pending(self) -> list[ReviewItem]:
        return [i for i in self._items.values() if i.status == "pending"]

    def get(self, item_id: str) -> ReviewItem | None:
        return self._items.get(item_id)

    def resolve(self, item_id: str, status: Any) -> bool:
        if item_id in self._items:
            self._items[item_id] = dataclasses.replace(self._items[item_id], status=status)
            return True
        return False


def _req() -> ExecApprovalRequested:
    return ExecApprovalRequested(run_id="r1", capability="Bash(npm test)", rationale="run tests")


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_policy_auto_approve_skips_the_inbox() -> None:
    gov = MockGovernanceProvider()
    queue = _MemQueue()  # nothing preset; if we reached the inbox we'd time out
    decision = await resolve_relay(
        _req(),
        agent_id="root",
        topology_id="t",
        governance=_AllowGov(),
        review_queue=cast(ReviewQueue, queue),
        max_wait_seconds=0,
        sleep=_no_sleep,
    )
    assert decision.granted and decision.responder == "policy"
    assert not queue.list_pending()  # never queued
    _ = gov


@pytest.mark.asyncio
async def test_inbox_approval_grants() -> None:
    gov = _DenyGov()
    queue = _MemQueue(preset="approved")
    decision = await resolve_relay(
        _req(),
        agent_id="root",
        topology_id="t",
        governance=gov,
        review_queue=cast(ReviewQueue, queue),
        max_wait_seconds=5,
        sleep=_no_sleep,
    )
    assert decision.granted and decision.responder == "operator"
    # audited: request + response
    types = [e.event_type for e in gov.events]
    assert "executor.approval_requested" in types and "executor.approval_response" in types


@pytest.mark.asyncio
async def test_inbox_rejection_denies() -> None:
    queue = _MemQueue(preset="rejected")
    decision = await resolve_relay(
        _req(),
        agent_id="root",
        topology_id="t",
        governance=_DenyGov(),
        review_queue=cast(ReviewQueue, queue),
        max_wait_seconds=5,
        sleep=_no_sleep,
    )
    assert not decision.granted and decision.responder == "operator"


@pytest.mark.asyncio
async def test_timeout_degrades_to_denial() -> None:
    # never resolved + a clock that jumps past the budget → timeout (never hangs)
    ticks = iter([0.0, 0.0, 100.0])

    decision = await resolve_relay(
        _req(),
        agent_id="root",
        topology_id="t",
        governance=_DenyGov(),
        review_queue=cast(ReviewQueue, _MemQueue()),
        max_wait_seconds=10,
        clock=lambda: next(ticks, 100.0),
        sleep=_no_sleep,
    )
    assert not decision.granted and decision.responder == "timeout" and decision.timed_out


class _AllowGov(MockGovernanceProvider):
    async def evaluate_action(self, **kwargs: Any) -> PolicyDecision:
        return PolicyDecision(allowed=True, reason="allowlisted", tier=1)


class _DenyGov(MockGovernanceProvider):
    """Policy denies the auto-approve, so relay falls through to the inbox."""

    async def evaluate_action(self, **kwargs: Any) -> PolicyDecision:
        return PolicyDecision(allowed=False, reason="not allowlisted", tier=1)


# ---- harness-node wiring -----------------------------------------------------------------------


class _FakeHarness(Executor):
    kind = "fake"

    def __init__(self, events_factory: Any) -> None:
        self._events_factory = events_factory

    def config_schema(self) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": True}

    def preflight(self, task: TaskSpec, sandbox: SandboxHandle) -> PreflightReport:
        return PreflightReport(ok=True)

    async def run(
        self, task: TaskSpec, sandbox: SandboxHandle, budget: BudgetEnvelope
    ) -> AsyncIterator[ExecEvent]:
        async for e in self._events_factory():
            yield e

    async def cancel(self, run_id: str) -> None:
        return None


class _FakeDriver(InteractionDriver):
    supports_relay = True

    def __init__(self) -> None:
        self.responses: list[bool] = []

    async def respond(self, request: ExecApprovalRequested, *, granted: bool) -> None:
        self.responses.append(granted)


def _agent() -> ResolvedAgent:
    return ResolvedAgent(
        id="root",
        role="root",
        model=None,
        prompt=None,
        skills=(),
        iam=None,
        executor=ResolvedExecutor(kind="fake"),
    )


def _state() -> SwarmState:
    return cast(SwarmState, {"input": "go", "agent_results": {}, "output": ""})


@pytest.mark.asyncio
async def test_harness_node_relay_approves_and_continues(tmp_path: Path) -> None:
    """With a relay driver + policy auto-approve, an approval request is resolved, fed back, and the
    stream continues to a success — not aborted."""

    async def events() -> AsyncIterator[ExecEvent]:
        yield ExecStarted(run_id="r1", kind="fake")
        yield ExecApprovalRequested(run_id="r1", capability="Bash(ls)", rationale="list")
        yield ExecMessage(role="assistant", text="did the thing")
        yield ExecResult(status="success", output="done")

    driver = _FakeDriver()
    # force the relay path: adapter would declare on_unanswerable=relay; here we patch the ctx via a
    # workspace adapter. Simpler: monkeypatch the resolved on_unanswerable through a relay adapter.
    ws = _relay_workspace(tmp_path)
    agent = dataclasses.replace(_agent(), executor=ResolvedExecutor(kind="relay-fake"))
    result = await run_harness_node(
        agent,
        _state(),
        _AllowGov(),
        workspace_root=ws,
        executor=_FakeHarness(events),
        driver=driver,
        review_queue=cast(ReviewQueue, _MemQueue()),
    )
    assert "done" in result["output"]  # ran to success, not aborted
    assert driver.responses == [True]  # the decision was fed back once


@pytest.mark.asyncio
async def test_harness_node_relay_without_driver_aborts(tmp_path: Path) -> None:
    async def events() -> AsyncIterator[ExecEvent]:
        yield ExecStarted(run_id="r1", kind="fake")
        yield ExecApprovalRequested(run_id="r1", capability="Bash(rm)", rationale="danger")
        yield ExecResult(status="success", output="should not reach")

    ws = _relay_workspace(tmp_path)
    agent = dataclasses.replace(_agent(), executor=ResolvedExecutor(kind="relay-fake"))
    result = await run_harness_node(
        agent,
        _state(),
        MockGovernanceProvider(),
        workspace_root=ws,
        executor=_FakeHarness(events),
        # no driver → NoInteractionDriver → relay unavailable → abort
    )
    assert "needs_approval" in result["output"]


def _relay_workspace(root: Path) -> Path:
    """A git workspace with an approved relay adapter, so the harness node sees on_unanswerable
    relay and the worktree sandbox can be provisioned."""

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    (root / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: w, name: W}\n"
    )
    adapters = root / "adapters"
    adapters.mkdir(exist_ok=True)
    (adapters / "relay-fake.yaml").write_text(
        "apiVersion: swarmkit/v1\n"
        "kind: ExecutorAdapter\n"
        "metadata: {id: relay-fake, name: Relay Fake, description: a relay adapter for tests}\n"
        "spec:\n"
        "  launch: {command: [relay-fake]}\n"
        "  stream: {format: jsonl}\n"
        "  event_map: [{emit: [{event: result, with: {status: success}}]}]\n"
        "  on_unanswerable: relay\n"
        "  interaction: {driver: hold-stream, max_approval_wait_seconds: 5}\n"
        "provenance: {authored_by: human, version: 0.1.0}\n"
    )
    # approve its launch so the gate doesn't block (_relay_ctx reads the spec)
    approve_launch(root, load_workspace_adapter_specs(root)["relay-fake"])
    git("init")
    git("config", "user.email", "t@t.test")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-m", "seed")
    return root
