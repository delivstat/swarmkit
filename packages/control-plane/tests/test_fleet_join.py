"""Panel-side Mode B (instance-initiated) join — design 19.

The panel can't reach a NAT'd instance, so the handshake inverts: the operator mints a one-time
join code, the edge calls POST /fleet/join with it + its full state, and the panel creates the
poll-mode Instance, issues the connector→panel credential (once), and caches the state. This mirrors
serve's Mode A /fleet/register on the panel side.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._join_code_store import JoinCodeStore
from swarmkit_control_plane._tables import join_code
from swarmkit_control_plane._tokens import token_hash

_STATE = {
    "workspace_id": "edge-oms",
    "schema_version": "1.7.0",
    "artifacts": {"topologies": [{"id": "t1"}], "skills": [], "archetypes": [], "triggers": []},
}
_OP = "operator-secret-token"


# --- JoinCodeStore ----------------------------------------------------------


def test_join_code_mint_and_consume_roundtrip(tmp_path: Path) -> None:
    store = JoinCodeStore(tmp_path / "cp.sqlite")
    code = store.mint(name="edge", endpoint="http://edge:8000", tier="run")
    consumed = store.consume(code)
    assert consumed == {"name": "edge", "endpoint": "http://edge:8000", "tier": "run"}


def test_join_code_is_single_use(tmp_path: Path) -> None:
    store = JoinCodeStore(tmp_path / "cp.sqlite")
    code = store.mint(name="edge")
    assert store.consume(code) is not None
    assert store.consume(code) is None  # replay rejected


def test_join_code_unknown_and_empty_are_none(tmp_path: Path) -> None:
    store = JoinCodeStore(tmp_path / "cp.sqlite")
    assert store.consume("never-minted") is None
    assert store.consume("") is None


def test_join_code_expired_is_none(tmp_path: Path) -> None:
    store = JoinCodeStore(tmp_path / "cp.sqlite")
    code = store.mint(name="edge", ttl_seconds=900)
    # backdate expiry so it's already past
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with store.engine.begin() as conn:
        conn.execute(
            join_code.update()
            .where(join_code.c.code_hash == token_hash(code))
            .values(expires_at=past)
        )
    assert store.consume(code) is None


# --- the /fleet/join + /fleet/join-code routes (open mode) ------------------


def _open_client(tmp_path: Path) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")
    return TestClient(create_app(registry))


def test_join_code_route_mints(tmp_path: Path) -> None:
    client = _open_client(tmp_path)
    r = client.post("/fleet/join-code", json={"name": "edge", "tier": "run"})
    assert r.status_code == 200, r.text
    assert r.json()["join_code"] and r.json()["tier"] == "run"


def test_join_code_rejects_bad_tier(tmp_path: Path) -> None:
    client = _open_client(tmp_path)
    assert client.post("/fleet/join-code", json={"tier": "root"}).status_code == 400


def test_join_creates_poll_instance_and_caches_state(tmp_path: Path) -> None:
    client = _open_client(tmp_path)
    code = client.post("/fleet/join-code", json={"name": "edge", "tier": "run"}).json()["join_code"]
    r = client.post(
        "/fleet/join",
        json={
            "join_code": code,
            "instance_identity": {"endpoint": "http://edge"},
            "instance_state": _STATE,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    iid = body["instance_id"]
    assert body["credential"]["type"] == "api_key" and body["credential"]["value"]
    assert body["credential"]["tier"] == "run"
    assert body["counts"]["topologies"] == 1
    # the panel created a poll-mode instance, healthy, with the identity's endpoint.
    inst = client.get(f"/instances/{iid}").json()
    assert inst["connection"] == "poll" and inst["health"] == "healthy"
    assert inst["endpoint"] == "http://edge" and inst["name"] == "edge"
    # its full state is cached (offline-inspectable).
    assert client.get(f"/instances/{iid}/state").json()["state"]["workspace_id"] == "edge-oms"


def test_join_identity_overrides_code_hints(tmp_path: Path) -> None:
    client = _open_client(tmp_path)
    code = client.post("/fleet/join-code", json={"name": "code-name"}).json()["join_code"]
    body = client.post(
        "/fleet/join",
        json={
            "join_code": code,
            "instance_identity": {"name": "identity-name"},
            "instance_state": _STATE,
        },
    ).json()
    assert client.get(f"/instances/{body['instance_id']}").json()["name"] == "identity-name"


def test_join_with_bad_or_used_code_is_401(tmp_path: Path) -> None:
    client = _open_client(tmp_path)
    assert client.post("/fleet/join", json={"join_code": "nope"}).status_code == 401
    code = client.post("/fleet/join-code", json={}).json()["join_code"]
    assert (
        client.post("/fleet/join", json={"join_code": code, "instance_state": _STATE}).status_code
        == 200
    )
    assert client.post("/fleet/join", json={"join_code": code}).status_code == 401  # replay


# --- auth-enforced mode -----------------------------------------------------


def _auth_client(tmp_path: Path) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")
    return TestClient(create_app(registry, operator_tokens=[_OP]))


def _op() -> dict[str, str]:
    return {"Authorization": f"Bearer {_OP}"}


def test_join_code_requires_operator(tmp_path: Path) -> None:
    client = _auth_client(tmp_path)
    # minting a join code is an operator action.
    assert client.post("/fleet/join-code", json={"name": "edge"}).status_code == 401
    assert client.post("/fleet/join-code", json={"name": "edge"}, headers=_op()).status_code == 200


def test_join_is_seam_exempt_and_issues_a_working_connector_token(tmp_path: Path) -> None:
    client = _auth_client(tmp_path)
    code = client.post(
        "/fleet/join-code", json={"name": "edge", "tier": "run"}, headers=_op()
    ).json()["join_code"]
    # the joining instance has no panel credential yet — the join code IS the auth (no op token).
    body = client.post("/fleet/join", json={"join_code": code, "instance_state": _STATE}).json()
    iid, key = body["instance_id"], body["credential"]["value"]
    conn = {"Authorization": f"Bearer {key}"}
    # the issued credential authenticates the connector's own poll...
    assert (
        client.post(f"/instances/{iid}/poll", json={"status": "ok"}, headers=conn).status_code
        == 200
    )
    # ...but is not an operator token — it can't mint further join codes.
    assert client.post("/fleet/join-code", json={}, headers=conn).status_code == 403
