"""A canary-routed job's outcome reaches the router's metrics.

The job's topology is the qualified name the router resolved (`hello@0.4.0`); the router keys its
metrics by the bare `hello`. `record_result` was called with the qualified name, found no metrics
for it and returned — so `total_runs` stayed 0 for every version and no canary could ever meet
`min_runs`. Drift was never passed either, so `drift_below` compared against a constant 0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.server._jobs import Job, execute_job

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: c, name: C}
governance: {provider: mock}
"""
_TOPO = """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: hello, version: %s}
agents:
  root:
    id: greeter
    role: root
    model: {provider: mock, name: m}
    prompt: {system: greet}
    intent_monitoring: {enabled: true, threshold: 0.75, on_drift: log}
"""


@pytest.mark.asyncio
async def test_result_and_drift_reach_the_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    (tmp_path / "topologies" / "hello").mkdir(parents=True)
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "topologies" / "hello" / "hello.yaml").write_text(_TOPO % "0.1.0")
    (tmp_path / "topologies" / "hello" / "hello-v0.2.0.yaml").write_text(_TOPO % "0.2.0")
    rt = WorkspaceRuntime.from_workspace_path(tmp_path)
    # Two versions of one topology resolve side by side (the other half of this fix).
    assert {"hello", "hello@0.1.0", "hello@0.2.0"} <= set(rt.workspace.topologies)

    router = CanaryRouter(
        [
            {
                "topology": "hello",
                "versions": [
                    {"version": "0.1.0", "weight": 50},
                    {"version": "0.2.0", "weight": 50},
                ],
            }
        ],
        {"hello": {"0.1.0", "0.2.0"}},
    )
    job: Any = Job(id="j1", topology="hello@0.2.0", status="pending", input="hi", version="0.2.0")
    await execute_job(job, rt, 10, canary_router=router)
    assert job.status == "completed"
    v = next(v for v in router.get_status()[0]["versions"] if v["version"] == "0.2.0")
    # On the unfixed code: total_runs 0 (recorded under "hello@0.2.0"), avg_drift 0.
    assert v["metrics"]["total_runs"] == 1
    assert v["metrics"]["avg_drift"] > 0


def test_version_keys_are_not_mcp_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`hello@0.2.0` is a registry key for the canary router, not a tool: `@` and `.` are outside
    the MCP tool-name alphabet and a client would refuse the list. Only bare names are exposed."""
    from swarmkit_runtime.mcp._serve import _build_handlers  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    (tmp_path / "topologies" / "hello").mkdir(parents=True)
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "topologies" / "hello" / "hello.yaml").write_text(_TOPO % "0.1.0")
    (tmp_path / "topologies" / "hello" / "hello-v0.2.0.yaml").write_text(_TOPO % "0.2.0")
    rt = WorkspaceRuntime.from_workspace_path(tmp_path)

    class _Tool:
        def __init__(self, **kw: Any) -> None:
            self.name = kw["name"]

    list_tools, _call = _build_handlers(
        {"c": rt}, {}, lambda _w, t: f"run_{t}", lambda _w: "search_knowledge", object, _Tool
    )
    import asyncio  # noqa: PLC0415

    names = {t.name for t in asyncio.run(list_tools())}
    assert "run_hello" in names
    assert not any("@" in n for n in names), names
