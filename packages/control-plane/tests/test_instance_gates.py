"""Federated harness gates (GET/POST /instances/{id}/review) — the fleet operator's view of §6.2
permission + §6.3 input gates paused on instances, resolved through the same /review API the CLI +
serve UI use. Live-pulled (Mode A / direct); a NAT'd Mode-B instance can't be federated inbound.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._connector import ConnectorError

_GATES: list[dict[str, Any]] = [
    {"id": "approval-1", "kind": "permission", "agent_id": "coder", "capability": "Bash(npm test)"},
    {
        "id": "input-1",
        "kind": "input",
        "agent_id": "coder",
        "question": "Which cache?",
        "options": ["redis", "memcached"],
    },
]


def _client(tmp_path: Path, gates_fn: Any = None, resolve_fn: Any = None) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {}

    if gates_fn is None:

        async def gates_fn(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
            return _GATES

    if resolve_fn is None:

        async def resolve_fn(
            endpoint: str, token_ref: str, item_id: str, action: str, answer: str
        ) -> dict[str, Any]:
            return {"id": item_id, "status": "approved", "answer": answer}

    return TestClient(create_app(registry, verify=verify, gates=gates_fn, resolve_gate=resolve_fn))


def _enroll(client: TestClient, connection: str) -> str:
    return str(
        client.post(
            "/instances",
            json={"name": "x", "endpoint": "http://serve:8000", "connection": connection},
        ).json()["id"]
    )


def test_direct_instance_surfaces_pending_gates(tmp_path: Path) -> None:
    seen: list[str] = []

    async def gates_fn(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        seen.append(endpoint)
        return _GATES

    client = _client(tmp_path, gates_fn)
    iid = _enroll(client, "direct")
    body = client.get(f"/instances/{iid}/review").json()

    assert body["reachable"] is True and body["reason"] is None
    assert [g["id"] for g in body["gates"]] == ["approval-1", "input-1"]
    assert seen == ["http://serve:8000"]  # pulled live, never stored


def test_poll_mode_instance_cannot_be_federated(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client, "poll")
    resp = client.get(f"/instances/{iid}/review")
    assert resp.status_code == 200
    assert resp.json() == {"reachable": False, "reason": "poll-mode", "gates": []}


def test_unreachable_instance_flips_health(tmp_path: Path) -> None:
    async def boom(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        raise ConnectorError("connection refused")

    client = _client(tmp_path, boom)
    iid = _enroll(client, "direct")
    assert client.get(f"/instances/{iid}/review").json()["reachable"] is False
    assert client.get(f"/instances/{iid}").json()["health"] == "unreachable"


def test_resolve_proxies_the_decision_to_the_instance(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []

    async def resolve_fn(
        endpoint: str, token_ref: str, item_id: str, action: str, answer: str
    ) -> dict[str, Any]:
        calls.append((item_id, action, answer))
        return {"id": item_id, "status": "approved", "answer": answer}

    client = _client(tmp_path, resolve_fn=resolve_fn)
    iid = _enroll(client, "direct")

    client.post(f"/instances/{iid}/review/approval-1/approve", json={})
    client.post(f"/instances/{iid}/review/input-1/answer", json={"answer": "redis"})

    assert ("approval-1", "approve", "") in calls
    assert ("input-1", "answer", "redis") in calls


def test_resolve_rejects_bad_action_and_poll_mode(tmp_path: Path) -> None:
    client = _client(tmp_path)
    direct = _enroll(client, "direct")
    assert client.post(f"/instances/{direct}/review/x/nope", json={}).status_code == 400
    poll = _enroll(client, "poll")
    assert client.post(f"/instances/{poll}/review/x/approve", json={}).status_code == 409


def test_unknown_instance_404(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/instances/nope/review").status_code == 404
