"""Tests for the growth-loop approval queue — the human gate (design §17)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import ArtifactStore, ProposalStore, SqliteRegistry, create_app

_OP = "operator-secret"


def _client(tmp_path: Path, *, enforce: bool = False) -> tuple[TestClient, ArtifactStore]:
    db = tmp_path / "registry.sqlite"
    registry = SqliteRegistry(db)
    artifacts = ArtifactStore(db)
    proposals = ProposalStore(db)

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    client = TestClient(
        create_app(
            registry,
            verify=verify,
            artifacts=artifacts,
            proposals=proposals,
            operator_tokens=[_OP] if enforce else None,
        )
    )
    return client, artifacts


def _propose(client: TestClient, headers: dict[str, str] | None = None) -> str:
    r = client.post(
        "/proposals",
        json={
            "kind": "skill",
            "artifact_id": "web-search",
            "content": {"category": "capability"},
            "proposed_by": "authoring-swarm",
            "signal": "gap",
        },
        headers=headers or {},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["id"])


def test_new_proposal_is_pending_and_publishes_nothing(tmp_path: Path) -> None:
    client, artifacts = _client(tmp_path)
    pid = _propose(client)
    assert client.get(f"/proposals/{pid}").json()["status"] == "pending"
    # Hard invariant: nothing is published until a human approves.
    assert artifacts.list_artifacts() == []


def test_approve_publishes_to_registry(tmp_path: Path) -> None:
    client, artifacts = _client(tmp_path)
    pid = _propose(client)
    resp = client.post(f"/proposals/{pid}/approve", json={"approved_by": "alice@example.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved" and body["published_version"] == "v1"
    assert body["approved_by"] == "alice@example.com"
    # The proposed content is now a registry version.
    arts = artifacts.list_artifacts()
    assert len(arts) == 1 and arts[0]["id"] == "web-search" and arts[0]["latest_version"] == "v1"


def test_cannot_re_decide(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    pid = _propose(client)
    client.post(f"/proposals/{pid}/approve", json={})
    assert client.post(f"/proposals/{pid}/approve", json={}).status_code == 409
    assert client.post(f"/proposals/{pid}/reject", json={}).status_code == 409


def test_reject_does_not_publish(tmp_path: Path) -> None:
    client, artifacts = _client(tmp_path)
    pid = _propose(client)
    resp = client.post(f"/proposals/{pid}/reject", json={"reason": "regresses smoke evals"})
    assert resp.status_code == 200 and resp.json()["status"] == "rejected"
    assert resp.json()["reason"] == "regresses smoke evals"
    assert artifacts.list_artifacts() == []


def test_list_by_status(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    p1 = _propose(client)
    _propose(client)
    client.post(f"/proposals/{p1}/approve", json={})
    assert len(client.get("/proposals?status=pending").json()) == 1
    assert len(client.get("/proposals?status=approved").json()) == 1


def test_machine_token_cannot_approve(tmp_path: Path) -> None:
    """The gate: a connector (machine) may not approve — reserved for human operators."""
    client, _ = _client(tmp_path, enforce=True)
    op = {"Authorization": f"Bearer {_OP}"}
    pid = _propose(client, op)
    iid = client.post(
        "/instances",
        json={"name": "edge", "endpoint": "n/a", "connection": "poll", "tier": "run"},
        headers=op,
    ).json()["id"]
    token = client.post(f"/instances/{iid}/mint-token", json={}, headers=op).json()["token"]
    conn = {"Authorization": f"Bearer {token}"}

    assert client.post(f"/proposals/{pid}/approve", json={}, headers=conn).status_code == 403
    # The operator (human-held credential) can.
    assert client.post(f"/proposals/{pid}/approve", json={}, headers=op).status_code == 200
