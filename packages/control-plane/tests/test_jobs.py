"""Tests for the federated live-jobs query (GET /instances/{id}/jobs)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._connector import ConnectorError

_JOBS = [
    {
        "job_id": "j1",
        "topology": "hello",
        "status": "running",
        "created_at": "2026-06-30T10:00:00Z",
    },
    {
        "job_id": "j2",
        "topology": "research",
        "status": "completed",
        "created_at": "2026-06-30T09:00:00Z",
    },
]


def _client(tmp_path: Path, jobs_fn: Any = None) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {}

    if jobs_fn is None:

        async def jobs_fn(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
            return _JOBS

    return TestClient(create_app(registry, verify=verify, jobs=jobs_fn))


def _enroll(client: TestClient, connection: str) -> str:
    return str(
        client.post(
            "/instances",
            json={"name": "x", "endpoint": "http://serve:8000", "connection": connection},
        ).json()["id"]
    )


def test_direct_instance_returns_live_jobs(tmp_path: Path) -> None:
    seen: list[str] = []

    async def jobs_fn(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        seen.append(endpoint)
        return _JOBS

    client = _client(tmp_path, jobs_fn)
    iid = _enroll(client, "direct")
    resp = client.get(f"/instances/{iid}/jobs")
    assert resp.status_code == 200
    assert resp.json()[0]["job_id"] == "j1"
    assert seen == ["http://serve:8000"]  # pulled from the instance, not stored


def test_poll_instance_is_409(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client, "poll")
    assert client.get(f"/instances/{iid}/jobs").status_code == 409


def test_unknown_instance_404(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/instances/nope/jobs").status_code == 404


def test_unreachable_instance_is_502(tmp_path: Path) -> None:
    async def boom(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        raise ConnectorError("connection refused")

    client = _client(tmp_path, boom)
    iid = _enroll(client, "direct")
    assert client.get(f"/instances/{iid}/jobs").status_code == 502
