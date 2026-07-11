"""Real end-to-end tests of the bundled adapters against the ACTUAL harness binaries.

Gated: each case runs only when ``SWARMKIT_E2E=1`` AND that harness's binary is on PATH (it makes a
real, billable subscription call). CI has neither, so every case skips there. This is the fidelity
check fixture tests can't give — it proves a bundled ``adapter.yaml`` maps the *real* stream, in
subscription mode, through the whole declarative engine.

Verified locally: claude-code (subscription) and opencode 1.17.x. codex / gemini-cli are not
installed here, so they stay experimental until someone runs them.

Run with ``SWARMKIT_E2E=1 uv run pytest packages/runtime/tests/test_harness_e2e.py`` (ensure the
harness is on PATH, e.g. ``export PATH="$HOME/.opencode/bin:$PATH"``).
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

_E2E = os.environ.get("SWARMKIT_E2E") == "1"

# adapter kind -> the binary it launches (spec.launch.command[0])
VERIFIED_HARNESSES = ["claude-code", "opencode"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", VERIFIED_HARNESSES)
async def test_real_harness_runs_in_subscription_mode(kind: str) -> None:
    spec = load_adapter_specs(None)[kind]
    binary = spec.launch.command[0]
    if not _E2E or shutil.which(binary) is None:
        pytest.skip(f"set SWARMKIT_E2E=1 and put {binary!r} on PATH to run the real e2e")

    # Default auth is subscription; the engine must strip any inherited api-key env so the CLI login
    # is used. Set a stale key to prove the precedence fix holds under the real binary.
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
    assert result.status == "success", (
        f"{kind}: expected success in subscription mode, got {result.status}"
    )
