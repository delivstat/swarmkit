"""Adopt an observed artifact into the deployable registry (design 20, Phase 3 slice 2).

The observed-state cache is kept separate from the registry; adopt is the explicit bridge — it reads
an artifact's content from the last-synced InstanceState and registers it as a registry version
(idempotent on content_hash), with provenance recording the source instance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import ArtifactStore, SqliteRegistry, create_app
from swarmkit_control_plane._routes_registry import _find_cached_artifact

_STATE = {
    "workspace_id": "edge-oms",
    "schema_version": "1.7.0",
    "artifacts": {
        "topologies": [
            {
                "id": "solution-design",
                "version": "1.0.0",
                "content_hash": "h1",
                "content": {"kind": "Topology", "nodes": ["root"]},
            }
        ],
        "skills": [
            {
                "id": "get-weather",
                "version": "1.0.0",
                "content_hash": "h2",
                "content": {"kind": "Skill"},
            }
        ],
        "archetypes": [],
        "triggers": [],
    },
}


def _client(tmp_path: Path) -> tuple[TestClient, ArtifactStore]:
    db = tmp_path / "registry.sqlite"
    registry = SqliteRegistry(db)
    artifacts = ArtifactStore(db)

    async def fetch_state(endpoint: str, token_ref: str) -> dict[str, Any]:
        return _STATE

    client = TestClient(create_app(registry, artifacts=artifacts, fetch_state=fetch_state))
    return client, artifacts


def _enroll_and_sync(client: TestClient) -> str:
    iid = client.post(
        "/instances",
        json={"name": "dc", "endpoint": "http://serve:8000", "connection": "direct"},
    ).json()["id"]
    assert client.post(f"/instances/{iid}/sync").status_code == 200
    return str(iid)


# --- the helper -------------------------------------------------------------


def test_find_cached_artifact_by_kind_and_id() -> None:
    found = _find_cached_artifact(_STATE, "skill", "get-weather")
    assert found is not None and found["content"] == {"kind": "Skill"}
    assert _find_cached_artifact(_STATE, "topology", "missing") is None
    assert _find_cached_artifact(_STATE, "workspace", "x") is None  # not an adoptable collection


# --- the /adopt route -------------------------------------------------------


def test_adopt_registers_a_registry_version_with_provenance(tmp_path: Path) -> None:
    client, artifacts = _client(tmp_path)
    iid = _enroll_and_sync(client)

    r = client.post(
        f"/instances/{iid}/adopt", json={"kind": "topology", "artifact_id": "solution-design"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == "v1" and body["adopted_from"] == iid
    # a real registry version now exists with the instance's content + adopt provenance.
    ver = artifacts.get_version("topology", "solution-design", "v1")
    assert ver is not None
    assert ver["content"] == {"kind": "Topology", "nodes": ["root"]}
    assert ver["authored_by"].startswith(f"adopted:instance/{iid}@")


def test_adopt_is_idempotent_on_content_hash(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    iid = _enroll_and_sync(client)
    v1 = client.post(
        f"/instances/{iid}/adopt", json={"kind": "skill", "artifact_id": "get-weather"}
    ).json()
    v2 = client.post(
        f"/instances/{iid}/adopt", json={"kind": "skill", "artifact_id": "get-weather"}
    ).json()
    assert v1["version"] == v2["version"]  # re-adopting unchanged content is a no-op


def test_adopt_unknown_artifact_is_404(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    iid = _enroll_and_sync(client)
    r = client.post(f"/instances/{iid}/adopt", json={"kind": "topology", "artifact_id": "nope"})
    assert r.status_code == 404


def test_adopt_non_adoptable_kind_is_400(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    iid = _enroll_and_sync(client)
    r = client.post(f"/instances/{iid}/adopt", json={"kind": "workspace", "artifact_id": "x"})
    assert r.status_code == 400


def test_adopt_before_sync_is_409(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    iid = client.post(
        "/instances",
        json={"name": "dc", "endpoint": "http://serve:8000", "connection": "direct"},
    ).json()["id"]
    r = client.post(
        f"/instances/{iid}/adopt", json={"kind": "topology", "artifact_id": "solution-design"}
    )
    assert r.status_code == 409


def test_adopt_unknown_instance_is_404(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.post("/instances/nope/adopt", json={"kind": "topology", "artifact_id": "x"})
    assert r.status_code == 404
