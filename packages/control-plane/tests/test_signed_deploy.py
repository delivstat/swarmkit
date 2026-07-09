"""Panel signs deploys with its fleet identity (design 22) — the signature verifies serve-side.

Cross-package: the panel's DeployService signs ``deploy:kind:id:content_hash`` with its private key,
and serve's ``verify_signature`` accepts it against the panel's public key — proving the two agree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import ArtifactStore, SqliteRegistry, create_app
from swarmkit_control_plane._artifacts import content_hash


def _client(tmp_path: Path, captured: dict[str, Any]) -> TestClient:
    db = tmp_path / "registry.sqlite"
    registry = SqliteRegistry(db)
    artifacts = ArtifactStore(db)
    artifacts.register_version("topology", "hello", content={"nodes": ["root"]})  # v1

    async def deploy_fn(
        endpoint: str, token_ref: str, kind: str, aid: str, content: Any, **identity: Any
    ) -> dict[str, Any]:
        captured.update({"content": content, **identity})
        return {"deployed": aid}

    async def register_fn(
        endpoint: str, enroll_token: str, fleet_id: str, requested_scope: str | None, **_id: Any
    ) -> dict[str, Any]:
        return {
            "membership_id": "mem-1",
            "credential": {
                "type": "api_key",
                "value": "membership-secret",
                "scope": "manage",
                "fingerprint": "fp",
            },
            "instance_state": {"schema_version": "", "artifacts": {}},
        }

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {}

    return TestClient(
        create_app(
            registry, verify=verify, artifacts=artifacts, deploy=deploy_fn, register_fn=register_fn
        )
    )


def test_deploy_is_signed_and_verifies_on_serve(tmp_path: Path) -> None:
    from swarmkit_runtime.fleet import deploy_message, verify_signature  # noqa: PLC0415

    captured: dict[str, Any] = {}
    client = _client(tmp_path, captured)
    identity = client.get("/fleet/identity").json()

    iid = client.post(
        "/instances",
        json={
            "name": "dc",
            "endpoint": "http://serve:8000",
            "connection": "direct",
            "token_ref": "env:LEGACY",
        },
    ).json()["id"]
    client.post(f"/instances/{iid}/register", json={"enroll_token": "t"})

    resp = client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    )
    assert resp.status_code == 200, resp.text

    # the deploy carried a signature + the panel's fleet_id + a monotonic sequence...
    assert captured["fleet_id"] == identity["fleet_id"]
    assert captured["signature"]
    assert captured["deploy_seq"] >= 1
    # ...and the signature verifies serve-side over the content hash + the bound sequence.
    chash = content_hash(captured["content"])
    message = deploy_message("topology", "hello", chash, captured["deploy_seq"])
    assert verify_signature(identity["fleet_public_key"], captured["signature"], message) is True
    # a signature over a different sequence would not verify (the seq is bound in the signature).
    other = deploy_message("topology", "hello", chash, captured["deploy_seq"] + 1)
    assert verify_signature(identity["fleet_public_key"], captured["signature"], other) is False


def test_deploy_seq_store_is_monotonic(tmp_path: Path) -> None:
    from swarmkit_control_plane._deploy_seq import DeploySeqStore  # noqa: PLC0415

    store = DeploySeqStore(tmp_path / "cp.sqlite")
    seqs = [store.next() for _ in range(5)]
    assert seqs == [1, 2, 3, 4, 5]
    # a reopened store continues, never repeats.
    assert DeploySeqStore(tmp_path / "cp.sqlite").next() == 6


def test_consecutive_deploys_use_increasing_sequences(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}  # holds the latest deploy's captured args
    client = _client(tmp_path, captured)
    iid = client.post(
        "/instances",
        json={
            "name": "dc",
            "endpoint": "http://s:8000",
            "connection": "direct",
            "token_ref": "env:L",
        },
    ).json()["id"]
    client.post(f"/instances/{iid}/register", json={"enroll_token": "t"})
    body = {"kind": "topology", "artifact_id": "hello", "version": "v1"}
    client.post(f"/instances/{iid}/deploy", json=body)
    first = captured["deploy_seq"]
    client.post(f"/instances/{iid}/deploy", json=body)
    assert captured["deploy_seq"] > first  # each deploy advances the sequence
