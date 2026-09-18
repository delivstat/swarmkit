"""The compiled graph is built once per topology and reused across runs (load-and-scale.md).

Compiling rebuilds the LangGraph graph — pure CPU on the event loop — so the run path caches it.
A reload builds a fresh runtime, so the cache is invalidated for free.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime

_WS = """\
apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: cc, name: CC}
governance: {provider: mock}
memory: {enabled: false}
"""
_TOPO = """\
apiVersion: swarmkit/v1
kind: Topology
metadata: {name: solo, version: 0.1.0}
agents:
  root: {id: a, role: root, model: {provider: mock, name: mock}, prompt: {system: hi}}
"""


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "topologies").mkdir()
    (tmp_path / "topologies" / "solo.yaml").write_text(_TOPO)
    return tmp_path


@pytest.mark.asyncio
async def test_the_run_path_compiles_once_and_reuses(tmp_path: Path) -> None:
    rt = WorkspaceRuntime.from_workspace_path(_ws(tmp_path))
    first = await rt._compiled("solo")
    second = await rt._compiled("solo")
    assert first is second, "the compiled graph is reused, not rebuilt per run"
    assert rt._graph_cache["solo"] is first


@pytest.mark.asyncio
async def test_concurrent_first_runs_compile_once(tmp_path: Path) -> None:
    rt = WorkspaceRuntime.from_workspace_path(_ws(tmp_path))
    graphs = await asyncio.gather(*[rt._compiled("solo") for _ in range(10)])
    assert all(g is graphs[0] for g in graphs), "the lock makes concurrent first-runs compile once"


@pytest.mark.asyncio
async def test_a_fresh_runtime_has_its_own_cache(tmp_path: Path) -> None:
    # A reload builds a new WorkspaceRuntime, so the cached graph does not leak across a reload.
    root = _ws(tmp_path)
    a = await WorkspaceRuntime.from_workspace_path(root)._compiled("solo")
    b = await WorkspaceRuntime.from_workspace_path(root)._compiled("solo")
    assert a is not b, "each runtime compiles its own graph; the cache is per instance"


@pytest.mark.asyncio
async def test_a_cached_graph_still_runs_correctly(tmp_path: Path) -> None:
    rt = WorkspaceRuntime.from_workspace_path(_ws(tmp_path))
    r1 = await rt.run("solo", "hello")
    r2 = await rt.run("solo", "again")  # second run uses the cached graph
    assert not r1.failed and not r2.failed
    assert rt._graph_cache.get("solo") is not None
