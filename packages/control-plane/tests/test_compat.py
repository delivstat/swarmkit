"""Tests for the schema-compatibility gate (design §15)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from swarmkit_control_plane import ArtifactStore, SqliteRegistry, create_app
from swarmkit_control_plane._compat import incompatibility, schema_compatible


@pytest.mark.parametrize(
    ("artifact", "instance", "compatible"),
    [
        ("1.6.0", "1.6.0", True),  # exact
        ("1.6.0", "1.7.0", True),  # instance newer minor
        ("1.7.0", "1.6.0", False),  # instance too old
        ("2.0.0", "1.9.0", False),  # different major → migration
        ("1.6.0", "2.0.0", False),  # different major
        ("", "1.6.0", True),  # unknown artifact schema → allowed
        ("1.6.0", "", True),  # unknown instance schema → allowed
        ("weird", "1.6.0", True),  # unparseable → allowed
    ],
)
def test_schema_compatible(artifact: str, instance: str, compatible: bool) -> None:
    assert schema_compatible(artifact, instance) is compatible
    assert (incompatibility(artifact, instance) is None) is compatible


# --- through the deploy endpoint ------------------------------------------------


def _client(tmp_path: Path, artifact_schema: str) -> TestClient:
    db = tmp_path / "registry.sqlite"
    registry = SqliteRegistry(db)
    artifacts = ArtifactStore(db)
    artifacts.register_version(
        "topology", "hello", content={"nodes": ["root"]}, schema_version=artifact_schema
    )

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    async def deploy(endpoint: str, tr: str, kind: str, aid: str, content: Any) -> dict[str, Any]:
        return {"deployed": aid}

    return TestClient(create_app(registry, verify=verify, artifacts=artifacts, deploy=deploy))


def _enroll_with_schema(client: TestClient, schema: str) -> str:
    iid = client.post(
        "/instances",
        json={"name": "edge", "endpoint": "n/a", "connection": "poll", "tier": "admin"},
    ).json()["id"]
    # heartbeat carries the instance's schema version
    client.post(f"/instances/{iid}/heartbeat", json={"status": "ok", "schema_version": schema})
    return str(iid)


def _deploy(client: TestClient, iid: str) -> int:
    return client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    ).status_code


def test_deploy_blocked_when_instance_schema_too_old(tmp_path: Path) -> None:
    client = _client(tmp_path, artifact_schema="1.7.0")
    iid = _enroll_with_schema(client, "1.6.0")
    assert _deploy(client, iid) == 409


def test_deploy_allowed_when_compatible(tmp_path: Path) -> None:
    client = _client(tmp_path, artifact_schema="1.6.0")
    iid = _enroll_with_schema(client, "1.7.0")
    assert _deploy(client, iid) == 200


def test_deploy_allowed_when_schema_unknown(tmp_path: Path) -> None:
    client = _client(tmp_path, artifact_schema="")  # artifact didn't record a schema
    iid = _enroll_with_schema(client, "1.6.0")
    assert _deploy(client, iid) == 200
