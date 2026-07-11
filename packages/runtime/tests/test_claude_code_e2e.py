"""Real end-to-end test of the bundled claude-code adapter against the actual `claude` binary.

Gated: runs only when `claude` is on PATH AND ``SWARMKIT_E2E_CLAUDE=1`` (it makes a real, billable
subscription call). CI has neither, so it skips there. This is the fidelity check fixture tests
can't give — it proves the bundled claude-code.yaml maps the *real* stream-json, in subscription
mode, through the whole declarative engine.

Run locally with ``SWARMKIT_E2E_CLAUDE=1 uv run pytest <this file>``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from swarmkit_runtime.executors import (
    BudgetEnvelope,
    ExecMessage,
    ExecResult,
    ExecStarted,
    SandboxHandle,
    TaskSpec,
    load_adapter_specs,
)
from swarmkit_runtime.executors._declarative import DeclarativeExecutor

_ENABLED = shutil.which("claude") is not None and os.environ.get("SWARMKIT_E2E_CLAUDE") == "1"
pytestmark = pytest.mark.skipif(
    not _ENABLED, reason="set SWARMKIT_E2E_CLAUDE=1 and install `claude` to run the real e2e"
)


@pytest.mark.asyncio
async def test_real_claude_code_runs_in_subscription_mode() -> None:
    spec = load_adapter_specs(None)["claude-code"]
    # Default auth is subscription; the engine must strip any inherited ANTHROPIC_API_KEY so the
    # CLI login is used. Set a stale key to prove the precedence fix holds under the real binary.
    os.environ["ANTHROPIC_API_KEY"] = "sk-stale-e2e-should-be-stripped"
    ex = DeclarativeExecutor(spec, config={})
    task = TaskSpec(statement="Reply with exactly the single word: PONG. Do not use any tools.")

    with tempfile.TemporaryDirectory() as d:
        events = [
            e
            async for e in ex.run(
                task,
                SandboxHandle(root=Path(d)),
                BudgetEnvelope(max_turns=1, max_wall_clock_minutes=3),
            )
        ]

    assert isinstance(events[0], ExecStarted)
    assert any(isinstance(e, ExecMessage) for e in events)
    result = next(e for e in events if isinstance(e, ExecResult))
    # A leaked stale key would be a billing failure — success proves subscription mode is active.
    assert result.status == "success", f"expected success in subscription mode, got {result.status}"
