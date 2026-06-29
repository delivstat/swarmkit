"""Tests for the artifact registry — versioning, provenance, deployments, drift."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import ArtifactStore, SqliteRegistry, create_app
from swarmkit_control_plane._artifacts import content_hash

_OP = "operator-secret"


def _store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "art.sqlite")


# --- store ----------------------------------------------------------------------


def test_register_autoincrements_and_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    v1 = store.register_version("topology", "hello", content={"nodes": [1]}, authored_by="alice")
    assert v1["version"] == "v1"
    # Same content → no new version.
    again = store.register_version("topology", "hello", content={"nodes": [1]})
    assert again["version"] == "v1"
    # Changed content → v2.
    v2 = store.register_version("topology", "hello", content={"nodes": [1, 2]})
    assert v2["version"] == "v2"
    assert v2["content_hash"] != v1["content_hash"]
    assert len(store.list_versions("topology", "hello")) == 2


def test_content_hash_is_canonical(tmp_path: Path) -> None:
    # Key order doesn't change the hash.
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_list_artifacts_reports_latest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_version("skill", "search", content="v1-body")
    store.register_version("skill", "search", content="v2-body")
    arts = store.list_artifacts()
    assert len(arts) == 1
    assert arts[0]["latest_version"] == "v2" and arts[0]["versions"] == 2


def test_deployment_requires_existing_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_version("topology", "hello", content={"x": 1})
    store.set_deployment("i1", "topology", "hello", "v1")
    assert store.list_deployments("i1")[0]["version"] == "v1"
    try:
        store.set_deployment("i1", "topology", "hello", "v99")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_drift_states(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_version("topology", "hello", content={"x": 1})  # v1
    store.register_version("topology", "hello", content={"x": 2})  # v2
    store.set_deployment("i1", "topology", "hello", "v2")

    # No report yet → missing.
    assert store.drift("i1")[0]["status"] == "missing"
    # Reports v1 → drift (intended v2).
    store.report("i1", [{"kind": "topology", "id": "hello", "version": "v1"}])
    assert store.drift("i1")[0]["status"] == "drift"
    # Reports v2 → ok.
    store.report("i1", [{"kind": "topology", "id": "hello", "version": "v2"}])
    assert store.drift("i1")[0]["status"] == "ok"


# --- API ------------------------------------------------------------------------


def _client(tmp_path: Path, *, enforce: bool = False) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")
    store = ArtifactStore(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    return TestClient(
        create_app(
            registry, verify=verify, artifacts=store, operator_tokens=[_OP] if enforce else None
        )
    )


def test_register_and_get_via_api(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.post(
        "/artifacts/topology/hello/versions",
        json={"content": {"nodes": []}, "authored_by": "alice@example.com"},
    )
    assert r.status_code == 200 and r.json()["version"] == "v1"
    got = client.get("/artifacts/topology/hello/versions/v1").json()
    assert got["authored_by"] == "alice@example.com" and got["content"] == {"nodes": []}
    assert client.get("/artifacts").json()[0]["id"] == "hello"


def test_unknown_kind_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/artifacts/bogus/x/versions", json={"content": "y"}).status_code == 404


def test_connector_may_report_only_its_own(tmp_path: Path) -> None:
    client = _client(tmp_path, enforce=True)
    op = {"Authorization": f"Bearer {_OP}"}
    iid = client.post(
        "/instances",
        json={"name": "edge", "endpoint": "n/a", "connection": "poll", "tier": "run"},
        headers=op,
    ).json()["id"]
    token = client.post(f"/instances/{iid}/mint-token", json={}, headers=op).json()["token"]
    conn = {"Authorization": f"Bearer {token}"}

    # Connector reports its own artifacts...
    own = client.post(
        f"/instances/{iid}/artifacts/report",
        json={"records": [{"kind": "topology", "id": "hello", "version": "v1"}]},
        headers=conn,
    )
    assert own.status_code == 200 and own.json()["reported"] == 1
    # ...but not another instance's, and can't register versions (operator-only).
    assert (
        client.post(
            "/instances/other/artifacts/report", json={"records": []}, headers=conn
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/artifacts/topology/hello/versions", json={"content": "x"}, headers=conn
        ).status_code
        == 403
    )
