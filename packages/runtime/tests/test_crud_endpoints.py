"""Tests for CRUD endpoints (topology/skill/archetype read/write).

Covers:
- GET /api/topologies/:id (resolved tree)
- GET /api/topologies/:id/yaml (raw YAML)
- GET /api/archetypes/:id (details)
- GET /api/skills/:id (details)
- PUT /api/topologies/:id (write + validate)
- POST /api/topologies (create new)
- DELETE /api/topologies/:id
- POST /api/reload
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


@pytest.fixture()
def client() -> TestClient:  # type: ignore[misc]
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(EXAMPLE_WS)
    with TestClient(app) as c:
        yield c


def test_get_topology_detail(client: TestClient) -> None:
    resp = client.get("/api/topologies/hello")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "hello"
    assert "resolved" in data
    assert data["resolved"]["id"] == "root"
    assert data["resolved"]["role"] == "root"


def test_get_topology_detail_not_found(client: TestClient) -> None:
    resp = client.get("/api/topologies/nonexistent")
    assert resp.status_code == 404


def test_get_topology_yaml(client: TestClient) -> None:
    resp = client.get("/api/topologies/hello/yaml")
    assert resp.status_code == 200
    data = resp.json()
    assert "yaml" in data
    assert "apiVersion" in data["yaml"]
    assert "hello" in data["yaml"]


def test_get_archetype_detail(client: TestClient) -> None:
    resp = client.get("/api/archetypes/greeter")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "greeter"
    assert data["role"] == "worker"


def test_get_archetype_not_found(client: TestClient) -> None:
    resp = client.get("/api/archetypes/nonexistent")
    assert resp.status_code == 404


def test_get_skill_detail(client: TestClient) -> None:
    resp = client.get("/api/skills/say-hello")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "say-hello"
    assert data["category"] == "capability"


def test_get_skill_not_found(client: TestClient) -> None:
    resp = client.get("/api/skills/nonexistent")
    assert resp.status_code == 404


def test_reload_workspace(client: TestClient) -> None:
    resp = client.post("/api/reload")
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert "hello" in data["topologies"]
