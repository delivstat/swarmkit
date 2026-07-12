"""The relay orchestrator + harness-node relay wiring (executor-relay-plan.md, relay PR2)."""

from __future__ import annotations

import dataclasses
import json
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
    PreflightReport,
    ResolvedExecutor,
    ResumeToken,
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
from swarmkit_runtime.model_providers import CompletionResponse, ContentBlock, Usage
from swarmkit_runtime.resolver import ResolvedAgent
from swarmkit_runtime.review import ReviewItem, ReviewQueue


class _MemQueue:
    """In-memory ReviewQueue; a preset decision (+ optional answer) resolves an item immediately."""

    def __init__(self, preset: str | None = None, preset_answer: str = "") -> None:
        self._items: dict[str, ReviewItem] = {}
        self._preset = preset  # "approved" | "rejected" | None
        self._preset_answer = preset_answer

    def submit(self, item: ReviewItem) -> None:
        if self._preset is not None:
            item = dataclasses.replace(item, status=self._preset, answer=self._preset_answer)  # type: ignore[arg-type]
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

    def answer_input(self, item_id: str, answer: str) -> bool:
        if item_id in self._items:
            self._items[item_id] = dataclasses.replace(
                self._items[item_id], status="approved", answer=answer
            )
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


class _ParkFake(Executor):
    """A park-resume fake: the first run hits a permission denial; a resumed run (with the grant)
    completes. Records the (resume_token, granted) of each run so the loop can be asserted."""

    kind = "fake"

    def __init__(self, capability: str = "Write") -> None:
        self._capability = capability
        self.runs: list[tuple[str | None, tuple[str, ...]]] = []

    def config_schema(self) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": True}

    def preflight(self, task: TaskSpec, sandbox: SandboxHandle) -> PreflightReport:
        return PreflightReport(ok=True)

    async def run(
        self,
        task: TaskSpec,
        sandbox: SandboxHandle,
        budget: BudgetEnvelope,
        *,
        resume_token: str | None = None,
        granted: tuple[str, ...] = (),
        answer: str | None = None,
    ) -> AsyncIterator[ExecEvent]:
        self.runs.append((resume_token, tuple(granted)))
        yield ExecStarted(run_id=f"r{len(self.runs)}", kind="fake")
        if resume_token is None:
            # first run: the capability is not granted → surface a denial, then stop.
            yield ExecApprovalRequested(
                run_id="r1", capability=self._capability, rationale="need it"
            )
            yield ExecResult(status="needs_approval", output="waiting on permission")
        else:
            yield ExecMessage(role="assistant", text="proceeding")
            yield ExecResult(status="success", output="completed")

    def resume_token(self, run_id: str) -> ResumeToken | None:
        return ResumeToken(value="sess-1")

    async def cancel(self, run_id: str) -> None:
        return None


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
async def test_park_resume_approves_then_completes(tmp_path: Path) -> None:
    """park-resume: a denial is auto-approved by policy, the harness is relaunched with the grant,
    and the resumed run completes — not aborted."""
    ws = _relay_workspace(tmp_path)
    agent = dataclasses.replace(_agent(), executor=ResolvedExecutor(kind="relay-fake"))
    fake = _ParkFake(capability="Write")
    result = await run_harness_node(
        agent,
        _state(),
        _AllowGov(),  # policy auto-approves the denial
        workspace_root=ws,
        executor=fake,
        review_queue=cast(ReviewQueue, _MemQueue()),
    )
    assert "completed" in result["output"]  # resumed run succeeded
    # two runs: the initial (no resume) and the relaunch carrying the approved capability
    assert len(fake.runs) == 2
    assert fake.runs[0] == (None, ())
    assert fake.runs[1] == ("sess-1", ("Write",))


@pytest.mark.asyncio
async def test_park_resume_denied_stays_needs_approval(tmp_path: Path) -> None:
    """When the inbox rejects (and policy denies), no relaunch happens and the denied result stands
    — never hangs."""
    ws = _relay_workspace(tmp_path)
    agent = dataclasses.replace(_agent(), executor=ResolvedExecutor(kind="relay-fake"))
    fake = _ParkFake()
    result = await run_harness_node(
        agent,
        _state(),
        _DenyGov(),  # policy denies → inbox
        workspace_root=ws,
        executor=fake,
        review_queue=cast(ReviewQueue, _MemQueue(preset="rejected")),
    )
    assert "needs_approval" in result["output"]
    assert len(fake.runs) == 1  # no relaunch


def _relay_workspace(root: Path) -> Path:
    """A git workspace with an approved park-resume relay adapter, so the harness node sees
    on_unanswerable relay + driver park-resume and the worktree sandbox can be provisioned."""

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
        "  interaction: {driver: park-resume, max_approval_wait_seconds: 5}\n"
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


# ---- §6.3 input-request escalation -------------------------------------------------------------


class _ClassifierModel:
    """A model provider whose structured output flags an input request (the classifier seat)."""

    provider_id = "fake"

    def supports(self, model: str) -> bool:
        return True

    async def complete(self, request: Any) -> CompletionResponse:
        payload = {
            "is_request": True,
            "question": "Which cache backend should I use?",
            "options": ["redis", "memcached"],
            "free_text_allowed": False,
        }
        return CompletionResponse(
            content=(ContentBlock(type="text", text=json.dumps(payload)),),
            stop_reason="end_turn",
            usage=Usage(),
        )


class _PuntThenAnswer(Executor):
    """First run: ends with a question and NO tool work (a punt-back). Resumed with the answer:
    completes. Records (resume_token, answer) per run."""

    kind = "fake"

    def __init__(self) -> None:
        self.runs: list[tuple[str | None, str | None]] = []

    def config_schema(self) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": True}

    def preflight(self, task: TaskSpec, sandbox: SandboxHandle) -> PreflightReport:
        return PreflightReport(ok=True)

    async def run(
        self,
        task: TaskSpec,
        sandbox: SandboxHandle,
        budget: BudgetEnvelope,
        *,
        resume_token: str | None = None,
        granted: tuple[str, ...] = (),
        answer: str | None = None,
    ) -> AsyncIterator[ExecEvent]:
        self.runs.append((resume_token, answer))
        yield ExecStarted(run_id=f"r{len(self.runs)}", kind="fake")
        if resume_token is None:
            yield ExecMessage(
                role="assistant", text="Should I use redis or memcached for the cache?"
            )
            yield ExecResult(status="needs_approval", output="waiting on a decision")
        else:
            yield ExecMessage(role="assistant", text=f"Using {answer}.")
            yield ExecResult(status="success", output=f"done with {answer}")

    def resume_token(self, run_id: str) -> ResumeToken | None:
        return ResumeToken(value="sess-1")

    async def cancel(self, run_id: str) -> None:
        return None


@pytest.mark.asyncio
async def test_input_request_classified_answered_and_resumed(tmp_path: Path) -> None:
    """A punt-back question is detected by the classifier, answered via the inbox, and the harness
    is relaunched with the answer — completing autonomously."""
    ws = _relay_workspace(tmp_path)
    agent = dataclasses.replace(
        _agent(),
        executor=ResolvedExecutor(kind="relay-fake", config={"classifier_model": "small"}),
    )
    fake = _PuntThenAnswer()
    gov = MockGovernanceProvider()
    result = await run_harness_node(
        agent,
        _state(),
        gov,
        workspace_root=ws,
        executor=fake,
        review_queue=cast(ReviewQueue, _MemQueue(preset="approved", preset_answer="redis")),
        model_provider=_ClassifierModel(),
    )
    assert "done with redis" in result["output"]
    assert len(fake.runs) == 2
    assert fake.runs[1] == ("sess-1", "redis")  # relaunched with the resolved answer
    types = [e.event_type for e in gov.events]
    assert "executor.input_requested" in types and "executor.input_response" in types


@pytest.mark.asyncio
async def test_input_request_no_model_provider_aborts(tmp_path: Path) -> None:
    """Without a model provider the classifier can't run, so a punt-back isn't detected — the run
    ends on its own terminal (needs_approval), never hangs, never relaunches."""
    ws = _relay_workspace(tmp_path)
    agent = dataclasses.replace(_agent(), executor=ResolvedExecutor(kind="relay-fake"))
    fake = _PuntThenAnswer()
    result = await run_harness_node(
        agent,
        _state(),
        MockGovernanceProvider(),
        workspace_root=ws,
        executor=fake,
        review_queue=cast(ReviewQueue, _MemQueue()),
        # no model_provider → no classification
    )
    assert "needs_approval" in result["output"]
    assert len(fake.runs) == 1  # no relaunch
