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
