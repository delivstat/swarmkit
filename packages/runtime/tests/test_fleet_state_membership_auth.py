"""serve GET /fleet/state accepts a membership key when auth is on (design 19).

A fleet reads its instance's state with the membership credential it was issued at enrollment —
not a shared serve transport token. When the transport-auth seam rejects the bearer, the middleware
falls back to membership auth for the fleet-read routes: a valid membership (monitor+) authorizes
the read; anything else stays denied. The transport-token path and open mode are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.auth import APIKeyAuthProvider
from swarmkit_runtime.server import create_app
from swarmkit_runtime.server._helpers import _membership_authenticates

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"

ADMIN_TOKEN = "admin-transport-token"  # test literal, not a real credential


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


def _auth_provider() -> APIKeyAuthProvider:
    return APIKeyAuthProvider(
        keys=[{"key_ref": ADMIN_TOKEN, "client_id": "operator", "tier": "admin"}]
    )


@pytest.fixture()
def open_client() -> TestClient:  # type: ignore[misc]
    """Open-mode serve (no auth) — /fleet/state is public (Phase 1)."""
    with TestClient(create_app(EXAMPLE_WS)) as c:
        yield c


@pytest.fixture()
def auth_client() -> TestClient:  # type: ignore[misc]
    """Auth-on serve — the transport seam enforces serve:* tiers; membership keys fall back in."""
    with TestClient(create_app(EXAMPLE_WS, auth_provider=_auth_provider())) as c:
        yield c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, scope: str = "monitor") -> str:
    """Mint an enroll token (admin) + register; return the issued membership key."""
    token = client.post(
        "/fleet/enroll-token", json={"scope": scope}, headers=_bearer(ADMIN_TOKEN)
    ).json()["token"]
    body = client.post(
        "/fleet/register", json={"fleet_id": "fleet-a"}, headers=_bearer(token)
    ).json()
    return str(body["credential"]["value"])


# --- the auth-seam fallback -------------------------------------------------


def test_open_mode_fleet_state_is_public(open_client: TestClient) -> None:
    # Phase 1 unchanged: with no auth provider the read needs no credential.
    assert open_client.get("/fleet/state").status_code == 200


def test_transport_token_still_reads_state(auth_client: TestClient) -> None:
    # Back-compat: a serve:read+ transport token authorizes /fleet/state as before.
    assert auth_client.get("/fleet/state", headers=_bearer(ADMIN_TOKEN)).status_code == 200


def test_missing_or_bad_bearer_is_denied(auth_client: TestClient) -> None:
    assert auth_client.get("/fleet/state").status_code == 401
    assert auth_client.get("/fleet/state", headers=_bearer("garbage")).status_code == 401


def test_membership_key_reads_state(auth_client: TestClient) -> None:
    key = _register(auth_client, scope="monitor")
    # the credential the fleet was issued authenticates the read — no serve token needed.
    assert auth_client.get("/fleet/state", headers=_bearer(key)).status_code == 200


def test_manage_scope_membership_also_reads(auth_client: TestClient) -> None:
    key = _register(auth_client, scope="manage")  # manage includes monitor's read
    assert auth_client.get("/fleet/state", headers=_bearer(key)).status_code == 200


def test_membership_key_does_not_open_other_routes(auth_client: TestClient) -> None:
    key = _register(auth_client, scope="monitor")
    # the fallback is scoped to fleet-read routes only — a membership key is not a serve token.
    assert auth_client.get("/capabilities", headers=_bearer(key)).status_code == 401
    assert auth_client.get("/jobs", headers=_bearer(key)).status_code == 401


def test_rotated_key_reads_old_key_does_not(auth_client: TestClient) -> None:
    key = _register(auth_client, scope="monitor")
    new_key = auth_client.post("/fleet/refresh", headers=_bearer(key)).json()["credential"]["value"]
    assert auth_client.get("/fleet/state", headers=_bearer(new_key)).status_code == 200
    assert auth_client.get("/fleet/state", headers=_bearer(key)).status_code == 401


# --- the helper in isolation ------------------------------------------------


class _FakeState:
    def __init__(self, store: Any) -> None:
        self.membership_store = store


class _FakeApp:
    def __init__(self, store: Any) -> None:
        self.state = _FakeState(store)


class _FakeRequest:
    def __init__(self, store: Any, headers: dict[str, str]) -> None:
        self.app = _FakeApp(store)
        self.headers = headers


def test_helper_rejects_non_fleet_paths() -> None:
    # path gating is independent of the store — a non-fleet-read path never accepts membership auth.
    assert _membership_authenticates(_FakeRequest(object(), {}), "GET", "/capabilities") is False


def test_helper_false_without_store() -> None:
    assert _membership_authenticates(_FakeRequest(None, {}), "GET", "/fleet/state") is False


def test_helper_false_without_bearer(tmp_path: Path) -> None:
    from swarmkit_runtime.fleet import MembershipStore  # noqa: PLC0415

    store = MembershipStore(tmp_path)
    assert _membership_authenticates(_FakeRequest(store, {}), "GET", "/fleet/state") is False


# --- manage-scope deploy over the membership credential (design 20) ----------


def _put_topology(client: TestClient, key: str) -> int:
    """Attempt a deploy PUT with a membership key; return the status code."""
    return client.put(
        "/api/topologies/hello",
        json={"yaml": "apiVersion: swarmkit/v1\nkind: Topology\n", "dry_run": True},
        headers=_bearer(key),
    ).status_code


def test_manage_membership_authorizes_deploy_put(auth_client: TestClient) -> None:
    key = _register(auth_client, scope="manage")
    # a manage key authenticates the deploy write route (dry-run PUT); 401/403 would mean the
    # scope gate rejected it. Any non-auth status (200/4xx from the handler) means auth passed.
    assert _put_topology(auth_client, key) not in (401, 403)


def test_monitor_membership_cannot_deploy(auth_client: TestClient) -> None:
    key = _register(auth_client, scope="monitor")
    # monitor may read /fleet/state but must NOT deploy.
    assert auth_client.get("/fleet/state", headers=_bearer(key)).status_code == 200
    assert _put_topology(auth_client, key) == 401


def test_transport_admin_token_still_deploys(auth_client: TestClient) -> None:
    # back-compat: a serve:admin transport token still authorizes the deploy PUT.
    assert _put_topology(auth_client, ADMIN_TOKEN) not in (401, 403)


def test_helper_manage_gates_deploy_put(tmp_path: Path) -> None:
    from swarmkit_runtime.fleet import MembershipStore  # noqa: PLC0415

    store = MembershipStore(tmp_path)
    _, manage_key = store.issue_membership("fleet-a", "manage")
    _, monitor_key = store.issue_membership("fleet-b", "monitor")

    def req(key: str) -> _FakeRequest:
        return _FakeRequest(store, {"Authorization": f"Bearer {key}"})

    # manage authenticates a deploy PUT; monitor does not; neither authenticates a POST create.
    assert _membership_authenticates(req(manage_key), "PUT", "/api/skills/x") is True
    assert _membership_authenticates(req(monitor_key), "PUT", "/api/skills/x") is False
    assert _membership_authenticates(req(manage_key), "POST", "/api/topologies") is False
