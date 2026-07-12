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
import subprocess
import tempfile
from pathlib import Path
from typing import cast

import pytest
from swarmkit_runtime.executors import (
    BudgetEnvelope,
    ExecMessage,
    ExecResult,
    ExecStarted,
    ResolvedExecutor,
    SandboxHandle,
    TaskSpec,
    load_adapter_specs,
)
from swarmkit_runtime.executors._declarative import DeclarativeExecutor
from swarmkit_runtime.governance import PolicyDecision
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._harness_node import run_harness_node
from swarmkit_runtime.langgraph_compiler._state import SwarmState
from swarmkit_runtime.resolver import ResolvedAgent
from swarmkit_runtime.review import ReviewItem, ReviewQueue

_E2E = os.environ.get("SWARMKIT_E2E") == "1"


class _MemQueue:
    """Minimal ReviewQueue stub — never exercised when policy auto-approves (it short-circuits)."""

    def submit(self, item: ReviewItem) -> None:
        return None

    def list_pending(self) -> list[ReviewItem]:
        return []

    def get(self, item_id: str) -> ReviewItem | None:
        return None

    def resolve(self, item_id: str, status: object) -> bool:
        return False


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


class _AllowGov(MockGovernanceProvider):
    """Auto-approves every relayed permission (stands in for the trust allowlist), so the
    park-resume e2e needs no human at the inbox."""

    async def evaluate_action(self, **kwargs: object) -> PolicyDecision:
        return PolicyDecision(allowed=True, reason="e2e allowlist", tier=1)


@pytest.mark.asyncio
async def test_real_claude_park_resume_relay(tmp_path: Path) -> None:
    """park-resume relay against the REAL claude binary: launch with a constrained grant (Read
    only), Write is denied and surfaces, policy auto-approves, the harness is relaunched with
    the Write grant, and the task completes — deny → approve → resume → complete, end to end."""
    if not _E2E or shutil.which("claude") is None:
        pytest.skip("set SWARMKIT_E2E=1 and put 'claude' on PATH to run the real e2e")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "seed.txt").write_text("seed\n")
    git("init")
    git("config", "user.email", "t@t.test")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-m", "seed")

    os.environ["ANTHROPIC_API_KEY"] = "sk-stale-e2e-should-be-stripped"  # subscription mode
    agent = ResolvedAgent(
        id="coder",
        role="worker",
        model=None,
        prompt=None,
        skills=(),
        iam=None,
        executor=ResolvedExecutor(kind="claude-code", config={"allowed_tools": "Read"}),
    )
    state = cast(
        SwarmState,
        {
            "input": "Create a file named out.txt containing the word HELLO using the Write tool.",
            "agent_results": {},
            "output": "",
        },
    )
    result = await run_harness_node(
        agent,
        state,
        _AllowGov(),
        workspace_root=tmp_path,
        review_queue=cast(ReviewQueue, _MemQueue()),
    )
    # If relay hadn't resolved + resumed, the run would end needs_approval (Write never granted).
    assert "needs_approval" not in result["output"], result["output"]
