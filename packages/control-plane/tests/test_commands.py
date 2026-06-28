"""Tests for the Mode B command queue (enqueue, poll/claim, result, tier bounds)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app


def _client(tmp_path: Path) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    return TestClient(create_app(registry, verify=verify))


def _enroll_poll(client: TestClient, *, tier: str = "run") -> str:
    resp = client.post(
        "/instances",
        json={"name": "edge", "endpoint": "n/a", "connection": "poll", "tier": tier},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["connection"] == "poll"
    assert body["tier"] == tier
    return str(body["id"])


def test_enqueue_then_poll_drains_and_dispatches(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll_poll(client)

    enq = client.post(f"/instances/{iid}/commands", json={"verb": "capabilities", "args": {}})
    assert enq.status_code == 200
    cmd_id = enq.json()["cmd_id"]
    assert enq.json()["status"] == "queued"

    poll = client.post(f"/instances/{iid}/poll", json={"status": "ok"})
    assert poll.status_code == 200
    cmds = poll.json()["commands"]
    assert len(cmds) == 1
    assert cmds[0] == {"cmd_id": cmd_id, "verb": "capabilities", "args": {}}

    # Claimed commands are no longer re-dispatched on the next poll.
    again = client.post(f"/instances/{iid}/poll", json={"status": "ok"})
    assert again.json()["commands"] == []


def test_poll_folds_heartbeat(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll_poll(client)
    assert client.get(f"/instances/{iid}").json()["health"] == "unknown"

    client.post(f"/instances/{iid}/poll", json={"status": "ok", "schema_version": "1.6.0"})
    inst = client.get(f"/instances/{iid}").json()
    assert inst["health"] == "healthy"
    assert inst["schema_version"] == "1.6.0"
    assert inst["last_seen"] is not None


def test_result_is_idempotent(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll_poll(client)
    cmd_id = client.post(f"/instances/{iid}/commands", json={"verb": "capabilities"}).json()[
        "cmd_id"
    ]
    client.post(f"/instances/{iid}/poll", json={"status": "ok"})

    first = client.post(
        f"/instances/{iid}/commands/{cmd_id}/result",
        json={"status": "done", "output": {"ok": True}},
    )
    assert first.status_code == 200
    assert first.json()["recorded"] is True

    # Second delivery (at-least-once) is accepted but does not overwrite.
    second = client.post(
        f"/instances/{iid}/commands/{cmd_id}/result",
        json={"status": "error", "error": "should be ignored"},
    )
    assert second.status_code == 200
    assert second.json()["recorded"] is False

    final = next(
        c for c in client.get(f"/instances/{iid}/commands").json() if c["cmd_id"] == cmd_id
    )
    assert final["status"] == "done"
    assert final["output"] == {"ok": True}


def test_enqueue_rejects_verb_above_tier(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll_poll(client, tier="read")
    # 'reload' needs admin; a read-tier instance may not be sent it.
    resp = client.post(f"/instances/{iid}/commands", json={"verb": "reload"})
    assert resp.status_code == 403
    assert "exceeds" in resp.json()["detail"]


def test_enqueue_rejects_unknown_verb(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll_poll(client)
    resp = client.post(f"/instances/{iid}/commands", json={"verb": "rm-rf"})
    assert resp.status_code == 400


def test_enqueue_rejects_direct_mode_instance(tmp_path: Path) -> None:
    client = _client(tmp_path)
    # Direct enroll skips pull-verify because our stub verify returns {}.
    iid = client.post(
        "/instances", json={"name": "dc", "endpoint": "http://dc:8000", "connection": "direct"}
    ).json()["id"]
    resp = client.post(f"/instances/{iid}/commands", json={"verb": "capabilities"})
    assert resp.status_code == 409


def test_invalid_tier_rejected_at_enroll(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post(
        "/instances",
        json={"name": "edge", "endpoint": "n/a", "connection": "poll", "tier": "superuser"},
    )
    assert resp.status_code == 400
