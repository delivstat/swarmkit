"""Drift is computed from what a sync observed, and a deploy keeps the adopted file's text.

Before: drift compared intended deployments against `reported_artifacts`, fed only by
POST /instances/{id}/artifacts/report — which no runtime ever called — so every deployment on every
real instance read "missing", even right after a successful deploy. Now a sync reports each synced
artifact's version + content hash, and drift compares hashes (registry `v1` and a file's `0.3.0`
are never the same label, but the same content hashes the same).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import ArtifactStore, SqliteRegistry, create_app
from swarmkit_control_plane._artifacts import content_hash
from swarmkit_control_plane._serve_client import ManifestUnsupported

_YAML = """\
apiVersion: swarmkit/v1
kind: Topology
metadata: {name: hello, version: 0.3.0}   # bumped for the canary
agents:
  root:
    id: greeter            # the only agent
    role: root
"""
_CONTENT: dict[str, Any] = {
    "apiVersion": "swarmkit/v1",
    "kind": "Topology",
    "metadata": {"name": "hello", "version": "0.3.0"},
    "agents": {"root": {"id": "greeter", "role": "root"}},
}


def _state(content: dict[str, Any], text: str = _YAML) -> dict[str, Any]:
    return {
        "workspace_id": "my-swarm",
        "schema_version": "1.44.0",
        "artifacts": {
            "topologies": [
                {
                    "id": "hello",
                    "version": str(content["metadata"]["version"]),
                    "content_hash": content_hash(content),
                    "content": content,
                    "yaml": text,
                }
            ],
            "skills": [],
            "archetypes": [],
            "triggers": [],
        },
    }


def _client(
    tmp_path: Path, states: list[dict[str, Any]]
) -> tuple[TestClient, list[dict[str, Any]]]:
    db = tmp_path / "registry.sqlite"
    registry = SqliteRegistry(db)
    artifacts = ArtifactStore(db)
    pushes: list[dict[str, Any]] = []

    async def fetch_state(endpoint: str, token_ref: str) -> dict[str, Any]:
        return states.pop(0) if len(states) > 1 else states[0]

    async def deploy(
        endpoint: str, token_ref: str, kind: str, aid: str, content: Any, **kw: Any
    ) -> dict[str, Any]:
        pushes.append({"kind": kind, "id": aid, "content": content, **kw})
        return {"valid": True}

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {"schema_version": "1.44.0"}

    async def no_manifest(endpoint: str, token_ref: str) -> dict[str, Any]:
        raise ManifestUnsupported("full pull")  # a resync takes the full-state path

    app = create_app(
        registry,
        artifacts=artifacts,
        fetch_state=fetch_state,
        fetch_manifest=no_manifest,
        deploy=deploy,
        verify=verify,
    )
    return TestClient(app), pushes


def _enroll_and_sync(client: TestClient) -> str:
    iid = client.post(
        "/instances",
        json={
            "name": "edge",
            "endpoint": "http://serve:8000",
            "connection": "direct",
            "token_ref": "tok",
        },
    ).json()["id"]
    assert client.post(f"/instances/{iid}/sync").status_code == 200
    return str(iid)


def test_adopt_then_deploy_is_ok_not_missing(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, [_state(_CONTENT)])
    iid = _enroll_and_sync(client)
    adopted = client.post(
        f"/instances/{iid}/adopt", json={"kind": "topology", "artifact_id": "hello"}
    ).json()
    assert adopted["version"] == "v1"
    r = client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    )
    assert r.status_code == 200, r.text
    # The instance runs exactly what the registry intends: same content, different labels.
    drift = client.get(f"/instances/{iid}/drift").json()
    assert drift == [
        {
            "kind": "topology",
            "id": "hello",
            "intended_version": "v1",
            "actual_version": "0.3.0",
            "status": "ok",
        }
    ]


def test_a_changed_file_on_the_instance_is_drift(tmp_path: Path) -> None:
    edited = {**_CONTENT, "agents": {"root": {"id": "greeter", "role": "root", "model": "x"}}}
    client, _ = _client(tmp_path, [_state(_CONTENT), _state(edited)])
    iid = _enroll_and_sync(client)
    client.post(f"/instances/{iid}/adopt", json={"kind": "topology", "artifact_id": "hello"})
    client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    )
    assert client.get(f"/instances/{iid}/drift").json()[0]["status"] == "ok"
    # Someone edits the file on the instance; the next sync sees a different hash.
    assert client.post(f"/instances/{iid}/sync").status_code == 200
    assert client.get(f"/instances/{iid}/drift").json()[0]["status"] == "drift"


def test_deploy_carries_the_adopted_text(tmp_path: Path) -> None:
    """The registry keeps the file an artifact was adopted from and the deploy sends it, so the
    instance can write it verbatim — comments and layout survive a fleet deploy."""
    client, pushes = _client(tmp_path, [_state(_CONTENT)])
    iid = _enroll_and_sync(client)
    client.post(f"/instances/{iid}/adopt", json={"kind": "topology", "artifact_id": "hello"})
    client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    )
    assert len(pushes) == 1
    assert pushes[0]["content"] == _CONTENT
    assert pushes[0]["source"] == _YAML
    assert "# the only agent" in pushes[0]["source"]


def test_a_version_registered_as_content_deploys_without_text(tmp_path: Path) -> None:
    client, pushes = _client(tmp_path, [_state(_CONTENT)])
    iid = _enroll_and_sync(client)
    client.post(
        "/artifacts/topology/hello/versions",
        json={"content": _CONTENT, "authored_by": "human"},
    )
    client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    )
    assert pushes[0]["source"] is None


def test_adopt_fetches_the_text_when_the_cache_has_none(tmp_path: Path) -> None:
    """A cache written before the text travelled (or by an older serve) has content only. Adopt
    asks the instance for the file so the version still deploys verbatim."""
    bare = _state(_CONTENT)
    del bare["artifacts"]["topologies"][0]["yaml"]
    asked: list[tuple[str, str]] = []

    async def artifact_yaml(endpoint: str, token_ref: str, plural: str, aid: str) -> str | None:
        asked.append((plural, aid))
        return _YAML

    db = tmp_path / "registry.sqlite"
    registry = SqliteRegistry(db)
    artifacts = ArtifactStore(db)
    pushes: list[dict[str, Any]] = []

    async def fetch_state(endpoint: str, token_ref: str) -> dict[str, Any]:
        return bare

    async def deploy(
        endpoint: str, token_ref: str, kind: str, aid: str, content: Any, **kw: Any
    ) -> dict[str, Any]:
        pushes.append({"kind": kind, "id": aid, **kw})
        return {"valid": True}

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {"schema_version": "1.44.0"}

    client = TestClient(
        create_app(
            registry,
            artifacts=artifacts,
            fetch_state=fetch_state,
            deploy=deploy,
            verify=verify,
            artifact_yaml=artifact_yaml,
        )
    )
    iid = _enroll_and_sync(client)
    client.post(f"/instances/{iid}/adopt", json={"kind": "topology", "artifact_id": "hello"})
    assert asked == [("topologies", "hello")]
    client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "topology", "artifact_id": "hello", "version": "v1"},
    )
    assert pushes[0]["source"] == _YAML
