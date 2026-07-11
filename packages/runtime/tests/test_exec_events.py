"""ExecEvent vocabulary + the Executor execution-hook contract (executor-abstraction §5, P2 PR1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.executors import (
    BudgetEnvelope,
    ExecArtifact,
    ExecResult,
    ExecStarted,
    ExecUsage,
    ModelExecutor,
    SandboxHandle,
    TaskSpec,
)


def _summarize(event: object) -> str:
    """Consumes an ExecEvent by its type — proves the union is usable by a projector."""
    match event:
        case ExecStarted(run_id=run_id):
            return f"started:{run_id}"
        case ExecUsage(cost_usd=cost):
            return f"usage:{cost}"
        case ExecResult(status=status):
            return f"result:{status}"
        case _:
            return "other"


def test_exec_events_carry_their_payloads() -> None:
    assert _summarize(ExecStarted(run_id="r1", kind="harness", ref="claude-code")) == "started:r1"
    assert _summarize(ExecUsage(input_tokens=10, output_tokens=2, cost_usd=0.06)) == "usage:0.06"
    art = ExecArtifact(artifact_kind="file_change", path="a.diff", mime="text/x-diff")
    res = ExecResult(status="success", output={"ok": True}, artifacts=(art,))
    assert _summarize(res) == "result:success"
    assert res.artifacts[0].artifact_kind == "file_change"


def test_exec_usage_defaults_are_zero_and_cost_nullable() -> None:
    u = ExecUsage()
    assert (u.unit, u.input_tokens, u.output_tokens, u.cost_usd) == ("tokens", 0, 0, None)


def test_model_executor_does_not_implement_the_harness_hooks() -> None:
    # `model` runs via the compiler's existing node, never through run()/preflight() — the defaults
    # raise so a misroute is loud, not silent.
    ex = ModelExecutor()
    task = TaskSpec(statement="do it")
    sandbox = SandboxHandle(root=Path("/tmp/x"))
    with pytest.raises(NotImplementedError, match="model"):
        ex.run(task, sandbox, BudgetEnvelope())
    with pytest.raises(NotImplementedError, match="model"):
        ex.preflight(task, sandbox)


@pytest.mark.asyncio
async def test_executor_cancel_and_resume_defaults() -> None:
    ex = ModelExecutor()
    await ex.cancel("r1")  # no-op default, must not raise
    assert ex.resume_token("r1") is None
