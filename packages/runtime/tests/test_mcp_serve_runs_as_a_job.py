"""`swarmkit mcp-serve` starts runs through JobService, like every other interface.

The standalone stdio MCP server called `WorkspaceRuntime.run` directly, so a run started from an
MCP client was not a job: no durable row (invisible in `/jobs`, the portal and cost attribution),
no canary routing, no capacity gate, no `source`. The twin path inside `swarmkit serve` was fixed;
this one was missed — the same way the trigger path beside it was once missed.

The pattern is what makes that keep happening: a second path into the runtime is a second set of
rules to keep in step, and it drifts in silence. The only symptom is a run behaving differently
depending on which door it came through.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.mcp import _serve


class _Result:
    output = "the answer"
    usage = None
    events: tuple[()] = ()


class _Runtime:
    """A workspace runtime with one topology and one canary route."""

    def __init__(self, *, canary: bool = False) -> None:
        self.ran: list[str] = []
        self.started = 0
        self.ended = 0
        self.store = None
        self._canary = canary
        self.workspace = type("_W", (), {"topologies": {"hello": object(), "hello@v2": object()}})()

    async def start_session(self) -> None:
        self.started += 1

    async def end_session(self) -> None:
        self.ended += 1

    async def run(self, topology: str, _input: str, **_kw: Any) -> Any:
        self.ran.append(topology)
        return _Result()


class _Text:
    def __init__(self, type: str = "text", text: str = "") -> None:
        self.type = type
        self.text = text


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    """The stores are process-wide and keyed by runtime id, which tmp objects reuse."""
    _serve._JOB_STORES.clear()
    _serve._SEMAPHORES.clear()
    yield
    _serve._JOB_STORES.clear()
    _serve._SEMAPHORES.clear()


@pytest.mark.asyncio
async def test_a_tool_call_creates_a_tracked_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("swarmkit_runtime.canary.router_for_workspace", lambda _ws: None)
    runtime = _Runtime()

    out = await _serve._run_topology(runtime, "hello", {"input": "hi"}, _Text)

    assert out[0].text.startswith("the answer")
    jobs = await _serve._JOB_STORES[id(runtime)].list_all()
    assert len(jobs) == 1, "a run from an MCP client must be a job like any other"
    assert jobs[0].topology == "hello"
    # `source` is what makes an assistant-driven run distinguishable from a served one.
    assert getattr(jobs[0], "source", "") == "mcp"


@pytest.mark.asyncio
async def test_a_tool_call_is_routed_to_the_canary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Canary is a property of the workspace, so it applies to this door too."""

    class _Router:
        def has_route(self, name: str) -> bool:
            return name == "hello"

        def select(self, _name: str) -> str:
            return "v2"

    monkeypatch.setattr("swarmkit_runtime.canary.router_for_workspace", lambda _ws: _Router())
    runtime = _Runtime()

    await _serve._run_topology(runtime, "hello", {"input": "hi"}, _Text)

    assert runtime.ran == ["hello@v2"]


@pytest.mark.asyncio
async def test_the_session_is_opened_and_closed_around_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP servers stay alive across calls; the run must not leak a session either way."""
    monkeypatch.setattr("swarmkit_runtime.canary.router_for_workspace", lambda _ws: None)
    runtime = _Runtime()

    await _serve._run_topology(runtime, "hello", {"input": "hi"}, _Text)

    assert (runtime.started, runtime.ended) == (1, 1)


@pytest.mark.asyncio
async def test_an_unknown_topology_answers_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool's contract is a string: a refusal is an answer, not a transport error."""
    monkeypatch.setattr("swarmkit_runtime.canary.router_for_workspace", lambda _ws: None)
    runtime = _Runtime()

    out = await _serve._run_topology(runtime, "nope", {"input": "hi"}, _Text)

    assert "nope" in out[0].text
    assert runtime.ended == 1, "the session is still closed on the refusal path"


@pytest.mark.asyncio
async def test_the_capacity_gate_is_shared_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """One semaphore per runtime, not one per call — a gate recreated per call bounds nothing."""
    monkeypatch.setattr("swarmkit_runtime.canary.router_for_workspace", lambda _ws: None)
    runtime = _Runtime()

    await _serve._run_topology(runtime, "hello", {"input": "one"}, _Text)
    first = _serve._SEMAPHORES[id(runtime)]
    await _serve._run_topology(runtime, "hello", {"input": "two"}, _Text)

    assert _serve._SEMAPHORES[id(runtime)] is first
