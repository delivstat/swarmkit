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
            endpoint: str, token_ref: str, kind: str, aid: str, content: Any, **_sig: Any
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
        "/instances",
        json={
            "name": "dc",
            "endpoint": "http://serve:8000",
            "connection": "direct",
            "token_ref": "env:DC_TOKEN",  # legacy fallback (no membership registered here)
        },
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
        endpoint: str, token_ref: str, kind: str, aid: str, content: Any, **_sig: Any
    ) -> dict[str, Any]:
        raise DeployError("connection refused")

    client, _ = _client(tmp_path, deploy_fn=boom)
    iid = client.post(
        "/instances",
        json={
            "name": "dc",
            "endpoint": "http://x",
            "connection": "direct",
            "token_ref": "env:X_TOKEN",  # legacy fallback path
        },
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


# --- deploy over the membership credential (design 20) ----------------------


def _manage_client(tmp_path: Path, *, scope: str = "manage") -> tuple[TestClient, list[str]]:
    """A client whose register_fn issues a *scope* membership; captures the credential each deploy
    PUT carries (the token_ref arg) so tests can assert which credential was used."""
    db = tmp_path / "registry.sqlite"
    registry = SqliteRegistry(db)
    artifacts = ArtifactStore(db)
    artifacts.register_version("topology", "hello", content={"nodes": ["root"]})

    used: list[str] = []

    async def deploy_fn(
        endpoint: str, token_ref: str, kind: str, aid: str, content: Any, **_sig: Any
    ) -> dict[str, Any]:
        used.append(token_ref)
        return {"deployed": aid}

    async def register_fn(
        endpoint: str, enroll_token: str, fleet_id: str, requested_scope: str | None, **_id: Any
    ) -> dict[str, Any]:
        return {
            "membership_id": "mem-1",
            "credential": {
                "type": "api_key",
                "value": "membership-secret",
                "scope": scope,
                "fingerprint": "fp1",
            },
            "instance_state": {"schema_version": "", "artifacts": {}},
        }

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {}  # stub the enroll-time pull-verify (no live serve in tests)

    client = TestClient(
        create_app(
            registry,
            verify=verify,
            artifacts=artifacts,
            deploy=deploy_fn,
            register_fn=register_fn,
        )
    )
    return client, used


def _enroll_direct(client: TestClient, token_ref: str = "") -> str:
    body: dict[str, Any] = {"name": "dc", "endpoint": "http://serve:8000", "connection": "direct"}
    if token_ref:
        body["token_ref"] = token_ref
    return str(client.post("/instances", json=body).json()["id"])


def test_deploy_uses_the_manage_membership_credential(tmp_path: Path) -> None:
    client, used = _manage_client(tmp_path, scope="manage")
    iid = _enroll_direct(client, token_ref="env:LEGACY")  # membership must win over token_ref
    assert client.post(f"/instances/{iid}/register", json={"enroll_token": "t"}).status_code == 200

    resp = client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    )
    assert resp.status_code == 200
    # the deploy carried the (decrypted) membership secret, NOT the legacy token_ref.
    assert used == ["membership-secret"]


def test_monitor_membership_cannot_deploy_is_403(tmp_path: Path) -> None:
    client, used = _manage_client(tmp_path, scope="monitor")
    iid = _enroll_direct(client, token_ref="env:LEGACY")
    client.post(f"/instances/{iid}/register", json={"enroll_token": "t"})
    # a monitor membership exists → refuse (no silent fallback to the admin token_ref).
    resp = client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    )
    assert resp.status_code == 403
    assert used == []


def test_deploy_without_membership_or_token_ref_is_409(tmp_path: Path) -> None:
    client, used = _manage_client(tmp_path)
    iid = _enroll_direct(client)  # no token_ref, no membership registered
    resp = client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    )
    assert resp.status_code == 409
    assert used == []
