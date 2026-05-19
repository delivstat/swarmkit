"""Tests for the SwarmKit HTTP server (M9).

Uses FastAPI's TestClient — no real HTTP server started. Tests
workspace loading, introspection endpoints, and run execution with
mock providers.
"""

from __future__ import annotations

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


# ---- health --------------------------------------------------------------


def test_health(hello_client: TestClient) -> None:
    resp = hello_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "hello-swarm" in data["workspace"]


# ---- introspection -------------------------------------------------------


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


# ---- run -----------------------------------------------------------------


def test_run_topology(hello_client: TestClient) -> None:
    resp = hello_client.post("/run/hello", json={"input": "Greet engineers"})
    assert resp.status_code == 200
    data = resp.json()
    assert "output" in data


def test_run_unknown_topology_returns_404(hello_client: TestClient) -> None:
    resp = hello_client.post("/run/nonexistent", json={"input": "test"})
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ---- reference workspace -------------------------------------------------


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
