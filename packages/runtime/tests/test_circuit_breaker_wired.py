"""`governance.limits` is enforced on a real run, not just modelled.

`GovernanceLimits` and `CircuitBreakerTracker` had unit tests and no caller: the schema accepted
`max_steps_per_agent: 20`, the README listed circuit breakers, the module docstring said "enforced
inside the compiler's agent execution loop", and no run ever consulted them. Found while checking
tutorial level 7 against the code.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.governance import (
    CircuitBreakerError,
    GovernanceLimits,
    limits_from_workspace,
)
from swarmkit_runtime.model_providers import (
    CompletionResponse,
    ContentBlock,
    MockModelProvider,
    Usage,
)

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: limited, name: Limited}
governance:
  provider: mock
  limits:
    max_steps_per_agent: 1
    max_steps_per_run: 2
"""
_TOPO = """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: chain, version: 0.1.0}
agents:
  root:
    id: lead
    role: root
    model: {provider: mock, name: m}
    prompt: {system: delegate}
    children:
      - id: a
        role: worker
        model: {provider: mock, name: m}
        prompt: {system: work}
      - id: b
        role: worker
        model: {provider: mock, name: m}
        prompt: {system: work}
        depends_on: [a]
"""


def _delegating(rt: WorkspaceRuntime) -> None:
    """Script the mock model so the lead delegates to `a` on every turn — the run only ends when
    something stops it, which is what a breaker test needs."""
    call = CompletionResponse(
        content=(
            ContentBlock(
                type="tool_use",
                tool_name="delegate_to_a",
                tool_input={"task": "do it"},
                tool_use_id="t1",
            ),
        ),
        stop_reason="tool_use",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    provider = MockModelProvider(responses={("m", "go"): call})
    registry = rt._provider_registry
    for key in list(getattr(registry, "_providers", {})):
        registry._providers[key] = provider


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "topologies").mkdir(parents=True)
    (ws / "workspace.yaml").write_text(_WS)
    (ws / "topologies" / "chain.yaml").write_text(_TOPO)
    return ws


def test_limits_are_read_from_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    rt = WorkspaceRuntime.from_workspace_path(_ws(tmp_path))
    limits = limits_from_workspace(rt.workspace.raw)
    assert limits == GovernanceLimits(max_steps_per_agent=1, max_steps_per_run=2)


def test_absent_block_keeps_the_defaults() -> None:
    class _Raw:
        governance = None

    assert limits_from_workspace(_Raw()) == GovernanceLimits()


@pytest.mark.asyncio
async def test_a_run_over_the_step_limit_is_stopped_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three nodes, a run limit of two: the third node entry raises, naming the limit."""
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    rt = WorkspaceRuntime.from_workspace_path(_ws(tmp_path))
    _delegating(rt)
    with pytest.raises(CircuitBreakerError) as exc:
        await rt.run("chain", "go")
    assert exc.value.limit_name in ("max_steps_per_run", "max_steps_per_agent")
    assert f"governance.limits.{exc.value.limit_name}" in str(exc.value)


@pytest.mark.asyncio
async def test_no_tracker_leaks_across_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swarmkit_runtime.governance import current_tracker  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    rt = WorkspaceRuntime.from_workspace_path(_ws(tmp_path))
    _delegating(rt)
    with pytest.raises(CircuitBreakerError):
        await rt.run("chain", "go")
    assert current_tracker() is None
