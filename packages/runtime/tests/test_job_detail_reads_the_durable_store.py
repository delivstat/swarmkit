"""Opening a job from history works, whichever store recorded it.

There are two job stores. `JobStore` is in memory: what THIS serve process started via
`POST /run/{topology}`, gone on restart. The durable store holds those *plus* every `swarmkit run`
(1.150.0) and every pipeline stage (1.152.0), and survives a restart.

`GET /jobs/{id}` read only the first. So the history table listed rows whose detail page 404'd —
the row came from `/jobs/history`, the page fetched `/jobs/{id}`, and the two are not the same
store. A CLI run appeared in the list and could not be opened; so could every job from before the
last restart. The list existing at all is what made this reachable: 1.150.0 put rows on screen that
1.150.0 gave no way to open.

The stream has the same split, and 404ing there makes a working page look broken — a finished job
has nothing live to follow, so it replays what was recorded and closes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from swarmkit_runtime.persistence._store import JobRow
from swarmkit_runtime.server._jobs import JobStore
from swarmkit_runtime.server._routes_jobs import _register_job_routes


class _DurableStore:
    """Only what the endpoint uses: a job lookup by id."""

    def __init__(self, rows: dict[str, JobRow] | None = None) -> None:
        self.rows = rows or {}

    def get_job(self, job_id: str) -> JobRow | None:
        return self.rows.get(job_id)


CLI_JOB = JobRow(
    id="92a6eaec-dfd0-4ba5-a6dd-520739e389d7",
    topology="wms-triage",
    status="completed",
    input="the ticket",
    output="three tables matched",
    created_at="2026-08-05T10:00:00Z",
    completed_at="2026-08-05T10:02:00Z",
)


def _client(durable: Any = None, job_store: JobStore | None = None) -> TestClient:
    app = FastAPI()
    _register_job_routes(app, job_store or JobStore())
    app.state.store = durable
    return TestClient(app)


# ---- the detail page opens -------------------------------------------------------------------


def test_a_cli_recorded_job_can_be_opened() -> None:
    """The bug, as reported: the row is listed, the link 404s."""
    client = _client(_DurableStore({CLI_JOB.id: CLI_JOB}))

    response = client.get(f"/jobs/{CLI_JOB.id}")

    assert response.status_code == 200
    assert response.json()["job_id"] == CLI_JOB.id


def test_the_durable_row_supplies_the_whole_response() -> None:
    """Not just a 200 — the page renders topology, status and output, and a blank page would be
    the same bug with a different status code."""
    client = _client(_DurableStore({CLI_JOB.id: CLI_JOB}))

    body = client.get(f"/jobs/{CLI_JOB.id}").json()

    assert body["topology"] == "wms-triage"
    assert body["status"] == "completed"
    assert body["output"] == "three tables matched"


def test_a_pipeline_stage_job_can_be_opened() -> None:
    """Stage jobs are durable-only too, and their ids are the ones `/runs` links to."""
    stage = JobRow(
        id="WMS-5:design",
        topology="design-swarm",
        status="completed",
        input="the ticket",
        correlation_id="WMS-5",
    )
    client = _client(_DurableStore({stage.id: stage}))

    assert client.get("/jobs/WMS-5:design").status_code == 200


def test_a_genuinely_unknown_job_is_still_a_404() -> None:
    """The fallback must not turn every id into a 200 — a wrong link should still say so."""
    client = _client(_DurableStore())

    assert client.get("/jobs/nope").status_code == 404


def test_no_durable_store_still_answers_from_memory() -> None:
    """A workspace with no store configured keeps working for jobs this process started."""
    client = _client(None)

    assert client.get("/jobs/nope").status_code == 404


@pytest.mark.asyncio
async def test_the_in_memory_store_still_wins() -> None:
    """A running job's live status must not be shadowed by a stale durable row — the row is
    written at creation and only updated at the end, so reading it first would report `running`
    forever."""
    memory = JobStore()
    job = await memory.create("wms-triage", "the ticket")
    job.status = "completed"
    job.output = "live answer"
    stale = JobRow(id=job.id, topology="wms-triage", status="running", input="the ticket")

    client = _client(_DurableStore({job.id: stale}), job_store=memory)
    body = client.get(f"/jobs/{job.id}").json()

    assert body["status"] == "completed"
    assert body["output"] == "live answer"


# ---- the stream does not break the page ------------------------------------------------------


def test_a_finished_job_streams_a_terminator_instead_of_404ing() -> None:
    """The page opens an EventSource as soon as it loads. A 404 there leaves it waiting on a dead
    connection — the job is over, so say so and close."""
    client = _client(_DurableStore({CLI_JOB.id: CLI_JOB}))

    text = client.get(f"/jobs/{CLI_JOB.id}/stream").text

    assert "[done] status=completed" in text


def test_a_replayed_stream_includes_the_recorded_events() -> None:
    row = JobRow(
        id="j9",
        topology="t",
        status="failed",
        input="i",
        events=["step 1", "step 2"],
    )
    client = _client(_DurableStore({row.id: row}))

    text = client.get("/jobs/j9/stream").text

    assert "data: step 1" in text
    assert "data: step 2" in text
    assert "[done] status=failed" in text


def test_an_unknown_job_still_404s_on_the_stream() -> None:
    client = _client(_DurableStore())

    assert client.get("/jobs/nope/stream").status_code == 404
