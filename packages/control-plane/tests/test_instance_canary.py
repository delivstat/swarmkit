"""Fleet canary monitor + control (design 26, Layer A) — federated status + promote/rollback.

Read is graceful (reachability envelope, like /runs); promote/rollback are Mode-A-only mutations
(404 / 409 poll-mode / 502 unreachable / result).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._connector import ConnectorError

_STATUS = {
    "enabled": True,
    "routes": [
        {
            "topology": "solution-design",
            "versions": [
                {"version": "1.0.0", "weight": 90},
                {
                    "version": "1.1.0",
                    "weight": 10,
                    "metrics": {"total_runs": 20, "failed_runs": 1, "error_rate": 0.05},
                },
            ],
        }
    ],
}


def _client(
    tmp_path: Path,
    *,
    canary_fn: Any = None,
    promote_fn: Any = None,
    rollback_fn: Any = None,
) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {}

    async def default_canary(endpoint: str, token_ref: str) -> dict[str, Any]:
        return _STATUS

    return TestClient(
        create_app(
            registry,
            verify=verify,
            canary=canary_fn or default_canary,
            canary_promote=promote_fn,
            canary_rollback=rollback_fn,
        )
    )


def _enroll(client: TestClient, connection: str = "direct") -> str:
    return str(
        client.post(
            "/instances",
            json={"name": "x", "endpoint": "http://serve:8000", "connection": connection},
        ).json()["id"]
    )


def test_canary_status_reachable(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client)
    body = client.get(f"/instances/{iid}/canary").json()
    assert body["reachable"] is True and body["reason"] is None
    assert body["canary"]["routes"][0]["topology"] == "solution-design"
    assert body["canary"]["routes"][0]["versions"][1]["metrics"]["error_rate"] == 0.05


def test_canary_status_poll_mode_is_reachable_false(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client, "poll")
    body = client.get(f"/instances/{iid}/canary").json()
    assert body == {
        "reachable": False,
        "reason": "poll-mode",
        "canary": {"enabled": False, "routes": []},
    }


def test_canary_status_unreachable_flips_health(tmp_path: Path) -> None:
    async def boom(endpoint: str, token_ref: str) -> dict[str, Any]:
        raise ConnectorError("refused")

    client = _client(tmp_path, canary_fn=boom)
    iid = _enroll(client)
    body = client.get(f"/instances/{iid}/canary").json()
    assert body["reachable"] is False and body["reason"] == "unreachable"
    assert client.get(f"/instances/{iid}").json()["health"] == "unreachable"


def test_canary_status_unknown_instance_404(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/instances/nope/canary").status_code == 404


def test_promote_forwards_to_instance(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def promote(endpoint: str, token_ref: str, topology: str, version: str) -> dict[str, Any]:
        seen.update(topology=topology, version=version)
        return {"promoted": True, "topology": topology, "version": version}

    client = _client(tmp_path, promote_fn=promote)
    iid = _enroll(client)
    resp = client.post(
        f"/instances/{iid}/canary/solution-design/promote", json={"version": "1.1.0"}
    )
    assert resp.status_code == 200 and resp.json()["promoted"] is True
    assert seen == {"topology": "solution-design", "version": "1.1.0"}


def test_promote_poll_mode_409(tmp_path: Path) -> None:
    client = _client(tmp_path, promote_fn=lambda *a: None)
    iid = _enroll(client, "poll")
    r = client.post(f"/instances/{iid}/canary/t/promote", json={"version": "1.1.0"})
    assert r.status_code == 409 and "Mode A" in r.json()["detail"]


def test_promote_unreachable_502(tmp_path: Path) -> None:
    async def boom(endpoint: str, token_ref: str, topology: str, version: str) -> dict[str, Any]:
        raise ConnectorError("down")

    client = _client(tmp_path, promote_fn=boom)
    iid = _enroll(client)
    r = client.post(f"/instances/{iid}/canary/t/promote", json={"version": "1.1.0"})
    assert r.status_code == 502


def test_rollback_forwards_to_instance(tmp_path: Path) -> None:
    async def rollback(endpoint: str, token_ref: str, topology: str) -> dict[str, Any]:
        return {"rolled_back": True, "topology": topology}

    client = _client(tmp_path, rollback_fn=rollback)
    iid = _enroll(client)
    resp = client.post(f"/instances/{iid}/canary/solution-design/rollback")
    assert resp.status_code == 200 and resp.json()["rolled_back"] is True
