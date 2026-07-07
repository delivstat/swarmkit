"""Observed-state cache: POST /instances/{id}/sync pulls + caches, GET .../state serves the cache.

Fleet enrollment Phase 1 (design 19): the panel pulls an instance's full /fleet/state and caches it,
so the inventory stays inspectable even when the instance is offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app

_STATE = {
    "apiVersion": "swarmkit/v1",
    "kind": "InstanceState",
    "workspace_id": "sterling-oms",
    "schema_version": "1.7.0",
    "artifacts": {
        "topologies": [
            {
                "id": "solution-design",
                "version": "1.0.0",
                "content_hash": "abc",
                "content": {"kind": "Topology"},
            }
        ],
        "skills": [
            {
                "id": "get-weather",
                "version": "1.0.0",
                "content_hash": "def",
                "content": {"kind": "Skill"},
            }
        ],
        "archetypes": [],
        "triggers": [],
    },
    "providers": ["anthropic"],
    "governance_provider": "mock",
    "health": {"status": "ok"},
}


def _client(tmp_path: Path, state_calls: list[str]) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    async def fetch_state(endpoint: str, token_ref: str) -> dict[str, Any]:
        state_calls.append(endpoint)
        return _STATE

    return TestClient(create_app(registry, verify=verify, fetch_state=fetch_state))


def _enroll(client: TestClient, connection: str = "direct") -> str:
    r = client.post(
        "/instances",
        json={
            "name": "edge",
            "endpoint": "http://edge:8000",
            "connection": connection,
            "tier": "read",
        },
    )
    assert r.status_code == 200, r.text
    return str(r.json()["id"])


def test_sync_pulls_and_caches_then_state_serves_it(tmp_path: Path) -> None:
    calls: list[str] = []
    client = _client(tmp_path, calls)
    iid = _enroll(client)

    synced = client.post(f"/instances/{iid}/sync")
    assert synced.status_code == 200
    body = synced.json()
    assert body["counts"] == {"topologies": 1, "skills": 1, "archetypes": 0, "triggers": 0}
    assert body["synced_at"]
    assert calls == ["http://edge:8000"]  # it actually pulled from the instance

    got = client.get(f"/instances/{iid}/state")
    assert got.status_code == 200
    cached = got.json()
    assert cached["state"]["workspace_id"] == "sterling-oms"
    assert cached["state"]["artifacts"]["topologies"][0]["content"] == {"kind": "Topology"}
    assert cached["synced_at"] == body["synced_at"]


def test_state_served_from_cache_when_instance_is_offline(tmp_path: Path) -> None:
    calls: list[str] = []
    client = _client(tmp_path, calls)
    iid = _enroll(client)
    client.post(f"/instances/{iid}/sync")  # cache it once

    # The cache read never touches the instance — so it works after it goes offline.
    n_before = len(calls)
    got = client.get(f"/instances/{iid}/state")
    assert got.status_code == 200 and got.json()["state"]["workspace_id"] == "sterling-oms"
    assert len(calls) == n_before  # no new pull


def test_state_404_before_first_sync(tmp_path: Path) -> None:
    client = _client(tmp_path, [])
    iid = _enroll(client)
    assert client.get(f"/instances/{iid}/state").status_code == 404


def test_sync_rejected_for_poll_mode(tmp_path: Path) -> None:
    client = _client(tmp_path, [])
    iid = _enroll(client, connection="poll")
    r = client.post(f"/instances/{iid}/sync")
    assert r.status_code == 409 and "Mode A" in r.json()["detail"]


def test_sync_unknown_instance_404(tmp_path: Path) -> None:
    client = _client(tmp_path, [])
    assert client.post("/instances/nope/sync").status_code == 404
