"""`swarmkit debug` has something to read: a run writes its prompts to the ring buffer.

`PromptRingBuffer` had a reader (`swarmkit debug`) and tests, and no writer on any run path, so
the command answered "No prompt ring buffer found" on every workspace that had ever run. Found
while checking tutorial level 8 against the code.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.telemetry import PromptRingBuffer

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: recorded, name: Recorded}
governance: {provider: mock}
"""
_TOPO = """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: hello, version: 0.1.0}
agents:
  root:
    id: greeter
    role: root
    model: {provider: mock, name: m}
    prompt: {system: You greet people warmly.}
"""


@pytest.mark.asyncio
async def test_a_run_records_its_prompts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    ws = tmp_path / "ws"
    (ws / "topologies").mkdir(parents=True)
    (ws / "workspace.yaml").write_text(_WS)
    (ws / "topologies" / "hello.yaml").write_text(_TOPO)
    rt = WorkspaceRuntime.from_workspace_path(ws)
    result = await rt.run("hello", "Hi, I am Alex")

    db = ws / ".swarmkit" / "prompts.sqlite"
    assert db.is_file(), "no ring buffer was written"
    entries = PromptRingBuffer(db_path=db).query_by_agent("greeter", last_n=5)
    assert entries, "the greeter's model call was not recorded"
    entry = entries[0]
    assert "You greet people warmly." in entry["prompt"]
    assert "Hi, I am Alex" in entry["prompt"]
    assert entry["response"] == "mock response"
    assert entry["model"] == "m"
    assert entry["run_id"] and entry["run_id"] in str(result.trace_data or entry["run_id"])
