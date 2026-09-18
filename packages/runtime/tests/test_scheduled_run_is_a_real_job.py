"""A cron trigger fires a run the same way `POST /run/{topology}` does.

The scheduler created an in-memory job with the trigger's source label as its INPUT and started
it around the job service: no durable row (gone from `/jobs/history` when it finished), no canary
version, no capacity gate, and the topology was asked the literal string `trigger:<id>`. Now a
trigger's `config.input` is the run's input and the run goes through `JobService.start`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: t, name: T}
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
_TRIGGER = """apiVersion: swarmkit/v1
kind: Trigger
metadata: {id: brief, name: Brief}
type: cron
targets: [hello]
config:
  expression: "* * * * *"
  input: "What changed overnight?"
"""


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    (tmp_path / "topologies").mkdir()
    (tmp_path / "triggers").mkdir()
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "topologies" / "hello.yaml").write_text(_TOPO)
    (tmp_path / "triggers" / "brief.yaml").write_text(_TRIGGER)
    return tmp_path


@pytest.mark.asyncio
async def test_the_fired_run_carries_the_input_and_lands_in_history(ws: Path) -> None:
    import httpx  # noqa: PLC0415
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws)
    async with app.router.lifespan_context(app):
        # The scheduler's own firing path, called directly rather than waiting for the minute.
        await app.state.scheduler._fire_fn("hello", "trigger:brief", "What changed overnight?")
        for _ in range(400):
            jobs = await app.state.job_store.list_all()
            if jobs and jobs[0].status == "completed":
                break
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            history = (await client.get("/jobs/history")).json()
            assert history, "the scheduled run must be in the durable history"
            row = history[0]
            assert row["topology"] == "hello" and row["source"] == "trigger:brief"
            job = (await client.get(f"/jobs/{row['job_id']}")).json()
    # On the unfixed code: no history row at all, and the input was "trigger:brief".
    assert job["input"] == "What changed overnight?"
    assert job["status"] == "completed"
