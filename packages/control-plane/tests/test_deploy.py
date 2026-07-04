"""Tests for governed deploy — push a published registry version onto an instance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import ArtifactStore, SqliteRegistry, create_app
from swarmkit_control_plane._deploy import DeployError

_OP = "operator-secret"


def _client(
    tmp_path: Path, *, deploy_fn: Any = None, enforce: bool = False
) -> tuple[TestClient, list[Any]]:
    db = tmp_path / "registry.sqlite"
    registry = SqliteRegistry(db)
    artifacts = ArtifactStore(db)
    artifacts.register_version("topology", "hello", content={"nodes": ["root"]})  # v1

    calls: list[Any] = []
    if deploy_fn is None:

        async def deploy_fn(
            endpoint: str, token_ref: str, kind: str, aid: str, content: Any
        ) -> dict[str, Any]:
            calls.append((endpoint, kind, aid, content))
            return {"deployed": aid}

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    client = TestClient(
        create_app(
            registry,
            verify=verify,
            artifacts=artifacts,
            deploy=deploy_fn,
            operator_tokens=[_OP] if enforce else None,
        )
    )
    return client, calls


def test_mode_a_pushes_records_intent_and_audits(tmp_path: Path) -> None:
    client, calls = _client(tmp_path)
    iid = client.post(
        "/instances", json={"name": "dc", "endpoint": "http://serve:8000", "connection": "direct"}
    ).json()["id"]

    resp = client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "direct"
    # pushed to the instance with the version's content
    assert calls == [("http://serve:8000", "topology", "hello", {"nodes": ["root"]})]
    # intent recorded
    deps = client.get(f"/instances/{iid}/deployments").json()
    assert deps[0]["version"] == "v1"
    # audited
    audit = client.get("/audit").json()
    assert any(a["action"] == "artifact.deploy" and a["version"] == "v1" for a in audit)


def test_mode_b_enqueues_deploy_command(tmp_path: Path) -> None:
    client, calls = _client(tmp_path)
    iid = client.post(
        "/instances",
        json={"name": "edge", "endpoint": "n/a", "connection": "poll", "tier": "admin"},
    ).json()["id"]

    resp = client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    )
    assert resp.status_code == 200 and resp.json()["mode"] == "poll"
    assert calls == []  # no direct push for poll instances
    cmds = client.get(f"/instances/{iid}/commands").json()
    assert cmds[0]["verb"] == "deploy"
    assert cmds[0]["args"]["kind"] == "topology" and cmds[0]["args"]["body"] == {"nodes": ["root"]}


def test_unknown_version_and_kind(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    iid = client.post(
        "/instances", json={"name": "x", "endpoint": "n/a", "connection": "poll"}
    ).json()["id"]
    assert (
        client.post(
            f"/instances/{iid}/deploy",
            json={"kind": "topology", "artifact_id": "hello", "version": "v9"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/instances/{iid}/deploy",
            json={"kind": "workspace", "artifact_id": "w", "version": "v1"},
        ).status_code
        == 400
    )


def test_deploy_failure_is_502(tmp_path: Path) -> None:
    async def boom(
        endpoint: str, token_ref: str, kind: str, aid: str, content: Any
    ) -> dict[str, Any]:
        raise DeployError("connection refused")

    client, _ = _client(tmp_path, deploy_fn=boom)
    iid = client.post(
        "/instances", json={"name": "dc", "endpoint": "http://x", "connection": "direct"}
    ).json()["id"]
    assert (
        client.post(
            f"/instances/{iid}/deploy",
            json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
        ).status_code
        == 502
    )
    # A failed push must NOT leave a phantom "deployed v1" record (drift would misreport it).
    assert client.get(f"/instances/{iid}/deployments").json() == []


def test_deploy_is_operator_only(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, enforce=True)
    op = {"Authorization": f"Bearer {_OP}"}
    iid = client.post(
        "/instances",
        json={"name": "edge", "endpoint": "n/a", "connection": "poll", "tier": "admin"},
        headers=op,
    ).json()["id"]
    token = client.post(f"/instances/{iid}/mint-token", json={}, headers=op).json()["token"]
    conn = {"Authorization": f"Bearer {token}"}
    body = {"kind": "topology", "artifact_id": "hello", "version": "v1"}
    assert client.post(f"/instances/{iid}/deploy", json=body, headers=conn).status_code == 403
    assert client.post(f"/instances/{iid}/deploy", json=body, headers=op).status_code == 200
