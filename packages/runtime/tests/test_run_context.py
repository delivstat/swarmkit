"""Per-run execution context isolates concurrent runs (review finding: core-engine
CRITICAL). Run-state dir and parent-agent are ContextVars, so two runs in one process
don't read/write each other's tasks.json / scope.json or corrupt trace attribution."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from swarmkit_runtime.langgraph_compiler._run_context import (
    current_parent_agent,
    reset_parent_agent,
    run_context,
    run_state_dir,
    set_parent_agent,
)


@pytest.mark.asyncio
async def test_run_context_isolates_concurrent_run_dirs(tmp_path: Path) -> None:
    seen: dict[str, Path] = {}

    async def run(rid: str) -> None:
        with run_context(tmp_path, rid):
            await asyncio.sleep(0.01)  # interleave the two runs
            (run_state_dir() / "tasks.json").write_text(rid)
            seen[rid] = run_state_dir()

    await asyncio.gather(run("run-aaaa"), run("run-bbbb"))

    # Different runs → different, id-namespaced dirs (not a shared "current/").
    assert seen["run-aaaa"] != seen["run-bbbb"]
    assert seen["run-aaaa"].name == "run-aaaa"
    # Each wrote its own file — no cross-corruption.
    assert (seen["run-aaaa"] / "tasks.json").read_text() == "run-aaaa"
    assert (seen["run-bbbb"] / "tasks.json").read_text() == "run-bbbb"


@pytest.mark.asyncio
async def test_parent_agent_is_context_scoped() -> None:
    results: dict[str, str | None] = {}

    async def run(name: str) -> None:
        token = set_parent_agent(name)
        await asyncio.sleep(0.01)
        results[name] = current_parent_agent()
        reset_parent_agent(token)

    await asyncio.gather(run("agent-A"), run("agent-B"))
    assert results == {"agent-A": "agent-A", "agent-B": "agent-B"}


def test_run_state_dir_falls_back_to_current_outside_a_run(tmp_path: Path) -> None:
    d = run_state_dir(tmp_path)
    assert d == tmp_path / ".swarmkit" / "run-state" / "current"
    assert d.is_dir()
