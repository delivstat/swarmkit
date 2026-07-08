"""GET /fleet/state — the full-content instance-state export (fleet enrollment Phase 1, doc 19).

Unlike /capabilities (names only), this returns every artifact's content + a content_hash so a fleet
can cache the instance's full inventory and adopt artifacts into its registry.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


@pytest.fixture()
def client() -> TestClient:  # type: ignore[misc]
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    with TestClient(create_app(EXAMPLE_WS)) as c:
        yield c


def test_fleet_state_envelope(client: TestClient) -> None:
    body = client.get("/fleet/state").json()
    assert body["apiVersion"] == "swarmkit/v1"
    assert body["kind"] == "InstanceState"
    assert body["workspace_id"] == "hello-swarm"
    assert body["schema_version"]  # populated from the installed schema package
    assert body["generated_at"]
    assert set(body["artifacts"]) == {"topologies", "skills", "archetypes", "triggers"}
    assert isinstance(body["providers"], list)


def test_fleet_state_carries_full_content_not_just_names(client: TestClient) -> None:
    arts = client.get("/fleet/state").json()["artifacts"]
    # hello-swarm ships a topology, a skill, and an archetype — each with real content.
    for kind in ("topologies", "skills", "archetypes"):
        assert arts[kind], f"expected at least one {kind}"
        entry = arts[kind][0]
        assert set(entry) >= {"id", "version", "content_hash", "content"}
        # content is the parsed artifact, not a name string.
        assert isinstance(entry["content"], dict)
        assert entry["content"].get("kind")  # every artifact YAML has a top-level kind
        assert entry["content"]["metadata"]["name"]


def test_fleet_state_hash_matches_registry_canonicalisation(client: TestClient) -> None:
    # content_hash must equal sha256 of sorted-keys compact JSON — the same hash the panel's
    # artifact registry uses, so an adopted artifact lines up.
    topo = client.get("/fleet/state").json()["artifacts"]["topologies"][0]
    expected = hashlib.sha256(
        json.dumps(topo["content"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert topo["content_hash"] == expected


def test_fleet_state_is_a_read_scoped_get(client: TestClient) -> None:
    # It's a plain GET (serve:read tier) — reachable like the other introspection routes.
    assert client.get("/fleet/state").status_code == 200


# --- delta sync: manifest + body-fetch (design 19 §delta sync) ---------------


def test_manifest_is_names_and_hashes_without_content(client: TestClient) -> None:
    full = client.get("/fleet/state").json()
    manifest = client.get("/fleet/state/manifest").json()
    # same envelope + metadata as the full state...
    assert manifest["kind"] == "InstanceState"
    assert manifest["workspace_id"] == full["workspace_id"]
    assert set(manifest["artifacts"]) == {"topologies", "skills", "archetypes", "triggers"}
    # ...but every entry keeps id/version/content_hash and drops content.
    entry = manifest["artifacts"]["topologies"][0]
    assert set(entry) == {"id", "version", "content_hash"}
    assert "content" not in entry
    # the hashes match the full state's, so a fleet can diff manifest-vs-cache.
    full_topo = full["artifacts"]["topologies"][0]
    assert entry["content_hash"] == full_topo["content_hash"] and entry["id"] == full_topo["id"]


def test_artifacts_fetch_returns_only_requested_bodies(client: TestClient) -> None:
    full = client.get("/fleet/state").json()
    topo_id = full["artifacts"]["topologies"][0]["id"]
    resp = client.post(
        "/fleet/state/artifacts",
        json={"refs": [{"collection": "topologies", "id": topo_id}]},
    )
    assert resp.status_code == 200, resp.text
    arts = resp.json()["artifacts"]
    # only the requested topology comes back with content; other collections are empty.
    assert [e["id"] for e in arts["topologies"]] == [topo_id]
    assert arts["topologies"][0]["content"]["metadata"]["name"]
    assert arts["skills"] == [] and arts["archetypes"] == []


def test_artifacts_fetch_empty_refs_returns_no_bodies(client: TestClient) -> None:
    arts = client.post("/fleet/state/artifacts", json={"refs": []}).json()["artifacts"]
    assert all(entries == [] for entries in arts.values())


def test_delta_endpoints_are_reads(client: TestClient) -> None:
    # both are serve:read (open mode here) — the POST is a content read, not a mutation.
    assert client.get("/fleet/state/manifest").status_code == 200
    assert client.post("/fleet/state/artifacts", json={"refs": []}).status_code == 200
