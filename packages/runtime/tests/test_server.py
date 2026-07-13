"""Tests for the SwarmKit HTTP server (M9).

Uses FastAPI's TestClient and httpx AsyncClient — no real HTTP server
started. Tests workspace loading, introspection endpoints, async job
execution, webhooks, and run execution with mock providers.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
REFERENCE_WS = REPO_ROOT / "reference"


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


@pytest.fixture()
def hello_client() -> TestClient:  # type: ignore[misc]
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(EXAMPLE_WS)
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def reference_client() -> TestClient:  # type: ignore[misc]
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(REFERENCE_WS)
    with TestClient(app) as client:
        yield client


# ---- health ------------------------------------------------------------------


def test_health(hello_client: TestClient) -> None:
    resp = hello_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "hello-swarm" in data["workspace"]


# ---- introspection -----------------------------------------------------------


def test_list_topologies(hello_client: TestClient) -> None:
    resp = hello_client.get("/topologies")
    assert resp.status_code == 200
    assert "hello" in resp.json()


def test_list_skills(hello_client: TestClient) -> None:
    resp = hello_client.get("/skills")
    assert resp.status_code == 200
    skills = resp.json()
    ids = {s["id"] for s in skills}
    assert "say-hello" in ids


def test_list_archetypes(hello_client: TestClient) -> None:
    resp = hello_client.get("/archetypes")
    assert resp.status_code == 200
    assert "greeter" in resp.json()


def test_validate(hello_client: TestClient) -> None:
    resp = hello_client.get("/validate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True


# ---- async job execution -----------------------------------------------------


def test_run_topology_returns_job(hello_client: TestClient) -> None:
    """POST /run/{topology} now returns a job_id instead of blocking."""
    resp = hello_client.post("/run/hello", json={"input": "Greet engineers"})
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "running"


def test_run_unknown_topology_returns_404(hello_client: TestClient) -> None:
    resp = hello_client.post("/run/nonexistent", json={"input": "test"})
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_get_job(hello_client: TestClient) -> None:
    """Submit a job, then poll it."""
    resp = hello_client.post("/run/hello", json={"input": "test"})
    job_id = resp.json()["job_id"]

    # Poll until done (with timeout)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        poll = hello_client.get(f"/jobs/{job_id}")
        assert poll.status_code == 200
        data = poll.json()
        if data["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)

    assert data["status"] in ("completed", "failed")
    assert data["job_id"] == job_id
    # the run-detail graph overlay needs to know which topology this run executed
    assert data["topology"] == "hello"


def test_get_nonexistent_job(hello_client: TestClient) -> None:
    resp = hello_client.get("/jobs/doesnotexist")
    assert resp.status_code == 404


def test_list_jobs(hello_client: TestClient) -> None:
    # Create a job first
    hello_client.post("/run/hello", json={"input": "list test"})

    resp = hello_client.get("/jobs")
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) >= 1
    assert "job_id" in jobs[0]
    assert "topology" in jobs[0]
    assert "status" in jobs[0]


# ---- webhook endpoint --------------------------------------------------------


def test_webhook_trigger(hello_client: TestClient) -> None:
    resp = hello_client.post("/hooks/hello", json={"input": "webhook test"})
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "running"


def test_webhook_unknown_topology(hello_client: TestClient) -> None:
    resp = hello_client.post("/hooks/nonexistent", json={"input": "test"})
    assert resp.status_code == 404


# ---- SSE streaming -----------------------------------------------------------


def test_job_stream(hello_client: TestClient) -> None:
    """Test that SSE endpoint streams events for a job."""
    # Create a job
    resp = hello_client.post("/run/hello", json={"input": "stream test"})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    # Wait for job to complete
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        poll = hello_client.get(f"/jobs/{job_id}")
        if poll.json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)

    # Now stream — job is done so SSE should return events immediately
    with hello_client.stream("GET", f"/jobs/{job_id}/stream") as stream:
        events: list[str] = []
        for line in stream.iter_lines():
            if line.startswith("data: "):
                events.append(line[6:])
                if "[done]" in line:
                    break

    assert len(events) >= 1
    assert any("[done]" in e for e in events)


# ---- reference workspace -----------------------------------------------------


def test_reference_lists_both_topologies(reference_client: TestClient) -> None:
    resp = reference_client.get("/topologies")
    assert resp.status_code == 200
    topos = resp.json()
    assert "code-review" in topos
    assert "skill-authoring" in topos


def test_reference_lists_all_skills(reference_client: TestClient) -> None:
    resp = reference_client.get("/skills")
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()}
    assert "github-repo-read" in ids
    assert "code-quality-review" in ids
    assert "run-tests" in ids


# ---- concurrent job limiting -------------------------------------------------


def test_concurrent_job_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the semaphore is fully acquired, POST /run returns 429."""
    import asyncio  # noqa: PLC0415

    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")

    app = create_app(EXAMPLE_WS)

    with TestClient(app) as client:
        # The semaphore is stored on app.state by the lifespan.
        # Manually exhaust it to simulate max concurrent jobs.
        sem: asyncio.Semaphore = app.state.job_semaphore
        # Acquire all slots (default 5, but just drain whatever is there)
        acquired = 0
        while not sem.locked():
            sem._value -= 1
            acquired += 1
        assert sem.locked()

        # Now a new job should be rejected with 429
        resp = client.post("/run/hello", json={"input": "should be rejected"})
        assert resp.status_code == 429
        assert "Max concurrent jobs" in resp.json()["detail"]

        # Restore semaphore state
        sem._value += acquired


# ---- server config parsing ---------------------------------------------------


def test_server_config_defaults() -> None:
    """ServerCfg defaults are applied when workspace has no server block."""
    from swarmkit_runtime.server import ServerCfg  # noqa: PLC0415

    cfg = ServerCfg()
    assert cfg.max_concurrent == 5
    assert cfg.timeout_seconds == 300
    assert cfg.mcp_enabled is True
