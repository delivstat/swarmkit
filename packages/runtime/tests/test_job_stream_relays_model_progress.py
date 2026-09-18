"""`GET /jobs/{id}/stream` carries a model agent's progress, not only "started" and "completed".

The job runner subscribed to the harness progress SINK; a model agent's lines ("[assistant]
thinking...", "[assistant] calling get-weather") go to the compiler's LISTENER bus, which the
conversation stream reads and the job stream did not. So a `POST /run/…` job was silent for its
whole duration and the portal's Event Stream showed two lines — the mirror image of the harness
blackout the sink was added to fix. Harness events are bridged onto the listener bus, so one
subscription now carries both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.server._jobs import Job, execute_job

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: s, name: S}
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
    prompt: {system: greet}
"""


@pytest.mark.asyncio
async def test_agent_progress_lines_reach_the_job_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    (tmp_path / "topologies").mkdir()
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "topologies" / "hello.yaml").write_text(_TOPO)
    rt = WorkspaceRuntime.from_workspace_path(tmp_path)
    job: Any = Job(id="j1", topology="hello", status="pending", input="hi")
    await execute_job(job, rt, 10)
    assert job.status == "completed"
    # On the unfixed code the events were exactly ["Job started …", "Job completed successfully"].
    assert any(line.startswith("[greeter] thinking") for line in job.events), job.events
    assert any(line.startswith("[greeter] done") for line in job.events), job.events
