"""Sync pulls the gap log and the audit tail (design 27), and the fleet knows funnels, contracts
and role registries.

Before: the panel's Gaps and Audit views were fed only by `POST /aggregate/*`, which no runtime
ever called. Now a sync folds `GET /gaps` and `GET /audit?since=<cursor>` into the same store,
best-effort, the way it already pulled `/usage` (design 23).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import ArtifactStore, SqliteRegistry, create_app
from swarmkit_control_plane._connector import ConnectorError
from swarmkit_control_plane._serve_client import ManifestUnsupported

_GAP = {
    "skill_id": "translate-text",
    "topology_id": "hello",
    "pattern": "agent 'assistant' called a tool it does not hold",
    "suggested_action": "author a 'translate-text' skill (swarmkit author skill)",
    "first_seen": "2026-09-18T02:00:00+00:00",
    "occurrences": 3,
}
_EVENTS = [
    {
        "event_id": "e2",
        "event_type": "run.completed",
        "agent_id": "assistant",
        "run_id": "r1",
        "timestamp": "2026-09-18T02:05:00+00:00",
        "payload": {},
    },
    {
        "event_id": "e1",
        "event_type": "skill.gap",
        "agent_id": "assistant",
        "run_id": "r1",
        "timestamp": "2026-09-18T02:04:00+00:00",
        "payload": {"skill_id": "translate-text"},
    },
]
_STATE = {
    "workspace_id": "my-swarm",
    "schema_version": "1.44.0",
    "artifacts": {
        "topologies": [],
        "skills": [],
        "archetypes": [],
        "triggers": [],
        "funnels": [
            {
                "id": "design-gate",
                "version": "1.0.0",
                "content_hash": "f1",
                "content": {"kind": "Funnel", "metadata": {"id": "design-gate"}},
                "yaml": "kind: Funnel  # two leads\n",
            }
        ],
        "contracts": [
            {
                "id": "handbook-finance",
                "version": "1.0.0",
                "content_hash": "c1",
                "content": {"kind": "Contract", "parties": ["handbook", "finance-portal"]},
                "yaml": "kind: Contract\n",
            }
        ],
        "roles": [
            {
                "id": "leads",
                "version": "",
                "content_hash": "r1",
                "content": {"kind": "RoleRegistry", "roles": []},
                "yaml": "kind: RoleRegistry\n",
            }
        ],
    },
}


class _Serve:
    """A stub instance: state, a gap log, an audit tail that honours `since`."""

    def __init__(self) -> None:
        self.gaps: list[dict[str, Any]] = [_GAP]
        self.events: list[dict[str, Any]] = list(_EVENTS)
        self.audit_calls: list[str | None] = []
        self.pushes: list[dict[str, Any]] = []

    async def state(self, endpoint: str, token_ref: str) -> dict[str, Any]:
        return _STATE

    async def fetch_gaps(self, endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        return self.gaps

    async def fetch_audit(
        self, endpoint: str, token_ref: str, since: str | None
    ) -> list[dict[str, Any]]:
        self.audit_calls.append(since)
        return [e for e in self.events if since is None or e["timestamp"] > since]

    async def deploy(
        self, endpoint: str, token_ref: str, kind: str, aid: str, content: Any, **kw: Any
    ) -> dict[str, Any]:
        self.pushes.append({"kind": kind, "id": aid, **kw})
        return {"valid": True}


def _client(tmp_path: Path, serve: _Serve, **kw: Any) -> TestClient:
    db = tmp_path / "registry.sqlite"

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {"schema_version": "1.44.0"}

    async def no_manifest(endpoint: str, token_ref: str) -> dict[str, Any]:
        raise ManifestUnsupported("full pull")  # a resync takes the full-state path

    return TestClient(
        create_app(
            SqliteRegistry(db),
            artifacts=ArtifactStore(db),
            verify=verify,
            fetch_state=serve.state,
            fetch_manifest=no_manifest,
            gaps_pull=kw.pop("gaps_pull", serve.fetch_gaps),
            audit_pull=kw.pop("audit_pull", serve.fetch_audit),
            deploy=serve.deploy,
            **kw,
        )
    )


def _enroll(client: TestClient) -> str:
    return str(
        client.post(
            "/instances",
            json={
                "name": "edge",
                "endpoint": "http://serve:8000",
                "connection": "direct",
                "token_ref": "tok",
            },
        ).json()["id"]
    )


def test_sync_pulls_gaps_into_the_fleet_rollup(tmp_path: Path) -> None:
    serve = _Serve()
    client = _client(tmp_path, serve)
    iid = _enroll(client)
    r = client.post(f"/instances/{iid}/sync").json()
    assert r["pulled_gaps"] == 3  # one row per occurrence
    ranked = client.get("/gaps").json()
    assert ranked[0]["capability"] == "translate-text"
    assert ranked[0]["occurrences"] == 3
    assert ranked[0]["instances"] == 1
    assert "author a 'translate-text' skill" in ranked[0]["description"]
    # An unchanged log on the next sync adds nothing.
    assert client.post(f"/instances/{iid}/sync").json()["pulled_gaps"] == 0


def test_sync_pulls_audit_after_a_cursor(tmp_path: Path) -> None:
    serve = _Serve()
    client = _client(tmp_path, serve)
    iid = _enroll(client)
    assert client.post(f"/instances/{iid}/sync").json()["pulled_audit"] == 2
    recent = client.get("/audit").json()
    assert [e["action"] for e in recent] == ["run.completed", "skill.gap"]
    assert recent[0]["instance_id"] == iid
    assert recent[0]["ts"] == "2026-09-18T02:05:00+00:00"
    # The second sync asks only for what is newer than the last event it saw…
    assert client.post(f"/instances/{iid}/sync").json()["pulled_audit"] == 0
    assert serve.audit_calls == [None, "2026-09-18T02:05:00+00:00"]
    # …and a new event arrives once.
    serve.events.insert(
        0,
        {
            "event_id": "e3",
            "event_type": "gate.resolved",
            "timestamp": "2026-09-18T02:06:00+00:00",
            "payload": {},
        },
    )
    assert client.post(f"/instances/{iid}/sync").json()["pulled_audit"] == 1
    assert len(client.get("/audit").json()) == 3


def test_a_failing_pull_does_not_fail_the_sync(tmp_path: Path) -> None:
    serve = _Serve()

    async def boom(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        raise ConnectorError("/gaps returned 404")  # an older serve without the route

    client = _client(tmp_path, serve, gaps_pull=boom)
    iid = _enroll(client)
    r = client.post(f"/instances/{iid}/sync")
    assert r.status_code == 200
    assert r.json()["pulled_gaps"] == 0
    assert r.json()["pulled_audit"] == 2
    assert client.get(f"/instances/{iid}").json()["health"] != "unreachable"


def test_funnels_contracts_and_roles_are_adoptable(tmp_path: Path) -> None:
    serve = _Serve()
    client = _client(tmp_path, serve)
    iid = _enroll(client)
    client.post(f"/instances/{iid}/sync")
    for kind, aid in (
        ("funnel", "design-gate"),
        ("contract", "handbook-finance"),
        ("role", "leads"),
    ):
        r = client.post(f"/instances/{iid}/adopt", json={"kind": kind, "artifact_id": aid})
        assert r.status_code == 200, r.text
        assert r.json()["version"] == "v1"
    assert client.get("/artifacts/role/leads/versions").json()[0]["version"] == "v1"


def test_a_funnel_deploys_a_role_registry_does_not(tmp_path: Path) -> None:
    serve = _Serve()
    client = _client(tmp_path, serve)
    iid = _enroll(client)
    client.post(f"/instances/{iid}/sync")
    client.post(f"/instances/{iid}/adopt", json={"kind": "funnel", "artifact_id": "design-gate"})
    client.post(f"/instances/{iid}/adopt", json={"kind": "role", "artifact_id": "leads"})
    ok = client.post(
        f"/instances/{iid}/deploy",
        json={"kind": "funnel", "artifact_id": "design-gate", "version": "v1"},
    )
    assert ok.status_code == 200, ok.text
    assert serve.pushes[-1]["kind"] == "funnel"
    assert serve.pushes[-1]["source"] == "kind: Funnel  # two leads\n"
    no = client.post(
        f"/instances/{iid}/deploy", json={"kind": "role", "artifact_id": "leads", "version": "v1"}
    )
    assert no.status_code == 400
