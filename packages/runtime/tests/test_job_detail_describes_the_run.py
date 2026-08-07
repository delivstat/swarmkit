"""A run's detail response says what the run WAS, not only what it returned.

`JobResponse` carried five fields — id, status, topology, output, error. So a run's page could show
what came back and nothing about the run that produced it: not when it started, not what it was
asked, not what it cost, not which pipeline it belonged to.

Both row shapes had all of it the whole time. The in-memory `Job` has `input`, `created_at` and
`completed_at`; the persisted `JobRow` adds version, source, correlation id and usage. The response
dropped them — the same shape as the audit API before 1.153.0, and as `mcp_tools` and
`context_files` before that: the data exists, the layer above does not pass it on.

The input matters most after the output. An answer is not reviewable without the question, and on a
pipeline stage it is the RESOLVED input — upstream artifacts and human decisions included.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from swarmkit_runtime.persistence._store import JobRow
from swarmkit_runtime.server._jobs import JobStore
from swarmkit_runtime.server._routes_jobs import _register_job_routes

DURABLE = JobRow(
    id="WMS-5:design",
    topology="design-swarm",
    status="completed",
    input="the ticket, plus the upstream spec",
    version="1.4.0",
    output="the draft",
    created_at="2026-08-07T10:00:00Z",
    completed_at="2026-08-07T10:04:00Z",
    usage_input_tokens=1200,
    usage_output_tokens=340,
    usage_cost_usd=0.42,
    correlation_id="WMS-5",
    source="pipeline",
)


class _DurableStore:
    def get_job(self, job_id: str) -> JobRow | None:
        return DURABLE if job_id == DURABLE.id else None


def _client(durable: Any = None, job_store: JobStore | None = None) -> TestClient:
    app = FastAPI()
    _register_job_routes(app, job_store or JobStore())
    app.state.store = durable
    return TestClient(app)


def _get(job_id: str = DURABLE.id) -> dict[str, Any]:
    body: dict[str, Any] = _client(_DurableStore()).get(f"/jobs/{job_id}").json()
    return body


# ---- what the run was --------------------------------------------------------------------------


def test_the_input_is_returned() -> None:
    """The most useful field after the output: an answer is not reviewable without the question."""
    assert _get()["input"] == "the ticket, plus the upstream spec"


def test_the_timestamps_are_returned() -> None:
    body = _get()

    assert body["created_at"] == "2026-08-07T10:00:00Z"
    assert body["completed_at"] == "2026-08-07T10:04:00Z"


def test_the_topology_and_version_are_returned() -> None:
    """Which swarm ran, and which version of it — a topology changes between runs."""
    body = _get()

    assert body["topology"] == "design-swarm"
    assert body["version"] == "1.4.0"


def test_the_provenance_is_returned() -> None:
    """Where the run came from, and the pipeline run it belongs to — so a stage links back."""
    body = _get()

    assert body["source"] == "pipeline"
    assert body["correlation_id"] == "WMS-5"


def test_the_usage_is_returned() -> None:
    body = _get()

    assert body["usage_input_tokens"] == 1200
    assert body["usage_cost_usd"] == 0.42


def test_the_original_fields_are_unchanged() -> None:
    """Additive only — every existing reader keeps working."""
    body = _get()

    assert body["job_id"] == "WMS-5:design"
    assert body["status"] == "completed"
    assert body["output"] == "the draft"


# ---- absent stays absent -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_live_job_reports_no_cost_rather_than_zero() -> None:
    """The in-memory row predates the durable columns, so a running job simply has no cost yet.
    Zero would read as "this run was free", which is a different and false claim."""
    memory = JobStore()
    job = await memory.create("hello", "say hi")

    body = _client(None, memory).get(f"/jobs/{job.id}").json()

    assert body["usage_cost_usd"] is None
    assert body["source"] is None


@pytest.mark.asyncio
async def test_a_live_job_still_reports_what_it_was_asked() -> None:
    """`input` and `created_at` exist on the in-memory row too — a job in flight is exactly when a
    reader wants to know what it is working on."""
    memory = JobStore()
    job = await memory.create("hello", "say hi")

    body = _client(None, memory).get(f"/jobs/{job.id}").json()

    assert body["input"] == "say hi"
    assert body["created_at"]


def test_an_unknown_job_is_still_a_404() -> None:
    assert _client(_DurableStore()).get("/jobs/nope").status_code == 404
