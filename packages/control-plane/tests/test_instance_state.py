"""Observed-state cache: POST /instances/{id}/sync pulls + caches, GET .../state serves the cache.

Fleet enrollment Phase 1 (design 19): the panel pulls an instance's full /fleet/state and caches it,
so the inventory stays inspectable even when the instance is offline.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._connector import ConnectorError, ManifestUnsupported

_STATE = {
    "apiVersion": "swarmkit/v1",
    "kind": "InstanceState",
    "workspace_id": "sterling-oms",
    "schema_version": "1.7.0",
    "artifacts": {
        "topologies": [
            {
                "id": "solution-design",
                "version": "1.0.0",
                "content_hash": "abc",
                "content": {"kind": "Topology"},
            }
        ],
        "skills": [
            {
                "id": "get-weather",
                "version": "1.0.0",
                "content_hash": "def",
                "content": {"kind": "Skill"},
            }
        ],
        "archetypes": [],
        "triggers": [],
    },
    "providers": ["anthropic"],
    "governance_provider": "mock",
    "health": {"status": "ok"},
}


async def _no_usage(endpoint: str, token_ref: str) -> dict[str, Any]:
    """Default usage stub — an instance with no recorded usage (empty by_model)."""
    return {"summary": {}, "by_model": []}


def _client(
    tmp_path: Path,
    state_calls: list[str],
    usage: Callable[[str, str], Awaitable[dict[str, Any]]] = _no_usage,
) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    async def fetch_state(endpoint: str, token_ref: str) -> dict[str, Any]:
        state_calls.append(endpoint)
        return _STATE

    return TestClient(create_app(registry, verify=verify, fetch_state=fetch_state, usage=usage))


def _enroll(client: TestClient, connection: str = "direct") -> str:
    r = client.post(
        "/instances",
        json={
            "name": "edge",
            "endpoint": "http://edge:8000",
            "connection": connection,
            "tier": "read",
        },
    )
    assert r.status_code == 200, r.text
    return str(r.json()["id"])


def test_sync_pulls_and_caches_then_state_serves_it(tmp_path: Path) -> None:
    calls: list[str] = []
    client = _client(tmp_path, calls)
    iid = _enroll(client)

    synced = client.post(f"/instances/{iid}/sync")
    assert synced.status_code == 200
    body = synced.json()
    assert body["counts"] == {"topologies": 1, "skills": 1, "archetypes": 0, "triggers": 0}
    assert body["synced_at"]
    assert calls == ["http://edge:8000"]  # it actually pulled from the instance

    got = client.get(f"/instances/{iid}/state")
    assert got.status_code == 200
    cached = got.json()
    assert cached["state"]["workspace_id"] == "sterling-oms"
    assert cached["state"]["artifacts"]["topologies"][0]["content"] == {"kind": "Topology"}
    assert cached["synced_at"] == body["synced_at"]


def test_state_served_from_cache_when_instance_is_offline(tmp_path: Path) -> None:
    calls: list[str] = []
    client = _client(tmp_path, calls)
    iid = _enroll(client)
    client.post(f"/instances/{iid}/sync")  # cache it once

    # The cache read never touches the instance — so it works after it goes offline.
    n_before = len(calls)
    got = client.get(f"/instances/{iid}/state")
    assert got.status_code == 200 and got.json()["state"]["workspace_id"] == "sterling-oms"
    assert len(calls) == n_before  # no new pull


def test_state_404_before_first_sync(tmp_path: Path) -> None:
    client = _client(tmp_path, [])
    iid = _enroll(client)
    assert client.get(f"/instances/{iid}/state").status_code == 404


def test_sync_rejected_for_poll_mode(tmp_path: Path) -> None:
    client = _client(tmp_path, [])
    iid = _enroll(client, connection="poll")
    r = client.post(f"/instances/{iid}/sync")
    assert r.status_code == 409 and "Mode A" in r.json()["detail"]


def test_sync_unknown_instance_404(tmp_path: Path) -> None:
    client = _client(tmp_path, [])
    assert client.post("/instances/nope/sync").status_code == 404


# --- usage pull-on-sync (design 23) ------------------------------------------


def test_sync_pulls_usage_into_the_fleet_rollup(tmp_path: Path) -> None:
    async def usage(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {
            "summary": {"total_calls": 3},
            "by_model": [
                {
                    "model": "kimi-k2",
                    "calls": 3,
                    "input_tokens": 300,
                    "output_tokens": 60,
                    "cost_usd": 1.5,
                }
            ],
        }

    client = _client(tmp_path, [], usage=usage)
    iid = _enroll(client)

    body = client.post(f"/instances/{iid}/sync").json()
    assert body["pulled_usage"] == 1  # one model row folded into the rollup

    rollup = client.get("/usage").json()
    assert len(rollup) == 1
    assert rollup[0]["model"] == "kimi-k2" and rollup[0]["input_tokens"] == 300


def test_resync_refreshes_usage_totals_not_doubles_them(tmp_path: Path) -> None:
    totals = {"n": 100}
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    async def fetch_state(endpoint: str, token_ref: str) -> dict[str, Any]:
        return _STATE

    async def fetch_manifest(endpoint: str, token_ref: str) -> dict[str, Any]:
        raise ManifestUnsupported("full-pull each sync for this test")

    async def usage(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {
            "by_model": [
                {"model": "m", "input_tokens": totals["n"], "output_tokens": 0, "cost_usd": 0}
            ]
        }

    client = TestClient(
        create_app(
            registry,
            verify=verify,
            fetch_state=fetch_state,
            fetch_manifest=fetch_manifest,
            usage=usage,
        )
    )
    iid = _enroll(client)

    client.post(f"/instances/{iid}/sync")
    totals["n"] = 250  # instance ran more between syncs (cumulative total grew)
    client.post(f"/instances/{iid}/sync")

    rollup = client.get("/usage").json()
    assert len(rollup) == 1 and rollup[0]["input_tokens"] == 250  # latest, not 100+250


def test_usage_pull_failure_does_not_fail_the_sync(tmp_path: Path) -> None:
    async def usage(endpoint: str, token_ref: str) -> dict[str, Any]:
        raise ConnectorError("boom")

    calls: list[str] = []
    client = _client(tmp_path, calls, usage=usage)
    iid = _enroll(client)

    resp = client.post(f"/instances/{iid}/sync")
    # State is the contract: the sync still succeeds and caches, usage just reports 0.
    assert resp.status_code == 200
    body = resp.json()
    assert body["pulled_usage"] == 0
    assert body["counts"]["topologies"] == 1
    assert client.get(f"/instances/{iid}/state").json()["state"]["workspace_id"] == "sterling-oms"
    assert client.get("/usage").json() == []


# --- delta sync at the route level (design 19 §delta sync) -------------------


def _delta_client(
    tmp_path: Path,
    manifest: dict[str, Any],
    fetched: dict[str, Any],
    fetch_calls: list[list[tuple[str, str]]],
) -> TestClient:
    """A client whose first sync full-pulls _STATE, and whose manifest/artifacts fns drive the
    second (delta) sync — capturing exactly which refs get their bodies fetched."""
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    async def fetch_state(endpoint: str, token_ref: str) -> dict[str, Any]:
        return _STATE

    async def fetch_manifest(endpoint: str, token_ref: str) -> dict[str, Any]:
        return manifest

    async def fetch_artifacts(
        endpoint: str, token_ref: str, refs: list[tuple[str, str]]
    ) -> dict[str, Any]:
        fetch_calls.append(refs)
        return fetched

    return TestClient(
        create_app(
            registry,
            verify=verify,
            fetch_state=fetch_state,
            fetch_manifest=fetch_manifest,
            fetch_artifacts=fetch_artifacts,
            usage=_no_usage,
        )
    )


def test_second_sync_delta_fetches_only_the_changed_body(tmp_path: Path) -> None:
    # manifest: the skill changed hash; the topology is unchanged.
    manifest = {
        **{k: v for k, v in _STATE.items() if k != "artifacts"},
        "artifacts": {
            "topologies": [{"id": "solution-design", "version": "1.0.0", "content_hash": "abc"}],
            "skills": [{"id": "get-weather", "version": "2.0.0", "content_hash": "def-NEW"}],
            "archetypes": [],
            "triggers": [],
        },
    }
    fetched = {
        "artifacts": {
            "topologies": [],
            "skills": [
                {
                    "id": "get-weather",
                    "version": "2.0.0",
                    "content_hash": "def-NEW",
                    "content": {"kind": "Skill", "updated": True},
                }
            ],
            "archetypes": [],
            "triggers": [],
        }
    }
    calls: list[list[tuple[str, str]]] = []
    client = _delta_client(tmp_path, manifest, fetched, calls)
    iid = _enroll(client)

    client.post(f"/instances/{iid}/sync")  # 1st: full pull
    resp = client.post(f"/instances/{iid}/sync").json()  # 2nd: delta

    assert resp["delta"] == {"mode": "delta", "fetched": 1, "reused": 1, "removed": 0}
    assert calls == [[("skills", "get-weather")]]  # only the changed skill body was fetched
    # the cache now has the new skill content but reused the unchanged topology.
    cached = client.get(f"/instances/{iid}/state").json()["state"]["artifacts"]
    assert cached["skills"][0]["content"] == {"kind": "Skill", "updated": True}
    assert cached["topologies"][0]["content"] == {"kind": "Topology"}
