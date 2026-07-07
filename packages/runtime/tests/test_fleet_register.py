"""serve /fleet/enroll-token + /fleet/register — the instance side of register (design 19).

A fleet mints a one-time enrollment token (admin), then registers with it to get a scoped membership
credential + the instance's full state, in one call. The token is single-use and authoritative for
scope.
"""

from __future__ import annotations

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


def _mint(client: TestClient, scope: str = "monitor") -> str:
    r = client.post("/fleet/enroll-token", json={"scope": scope})
    assert r.status_code == 200, r.text
    return str(r.json()["token"])


def test_register_returns_membership_credential_and_state(client: TestClient) -> None:
    token = _mint(client, "manage")
    r = client.post(
        "/fleet/register",
        json={"fleet_id": "fleet-a"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["membership_id"]
    cred = body["credential"]
    assert cred["type"] == "api_key" and cred["value"] and cred["scope"] == "manage"
    # the full state rides along — content, not just names.
    arts = body["instance_state"]["artifacts"]
    assert arts["topologies"] and arts["topologies"][0]["content"]


def test_enrollment_token_is_single_use(client: TestClient) -> None:
    token = _mint(client)
    ok = client.post(
        "/fleet/register",
        json={"fleet_id": "fleet-a"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 200
    # replaying the same token is rejected.
    replay = client.post(
        "/fleet/register",
        json={"fleet_id": "fleet-b"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert replay.status_code == 401


def test_register_without_token_is_401(client: TestClient) -> None:
    assert client.post("/fleet/register", json={"fleet_id": "x"}).status_code == 401


def test_register_with_bad_token_is_401(client: TestClient) -> None:
    r = client.post(
        "/fleet/register",
        json={"fleet_id": "x"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert r.status_code == 401


def test_enroll_token_rejects_unknown_scope(client: TestClient) -> None:
    assert client.post("/fleet/enroll-token", json={"scope": "root"}).status_code == 400


def test_scope_comes_from_the_token_not_the_request(client: TestClient) -> None:
    token = _mint(client, "monitor")  # minted as monitor
    r = client.post(
        "/fleet/register",
        json={"fleet_id": "fleet-a", "requested_scope": "manage"},  # can't self-upgrade
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.json()["credential"]["scope"] == "monitor"
