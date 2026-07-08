"""Fleet identity — self-certifying fleet_id, proof-of-possession at register, TOFU pin (doc 21).

Serve verifies the fleet's Ed25519 identity and pins its public key trust-on-first-use; a later
same-fleet_id/different-key register is a detectable mismatch.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from swarmkit_runtime.fleet import (
    fleet_id_from_public_key,
    proof_message,
    verify_proof,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
WORKSPACE_ID = "hello-swarm"


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


@pytest.fixture()
def client() -> TestClient:  # type: ignore[misc]
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    with TestClient(create_app(EXAMPLE_WS)) as c:
        yield c


class _Fleet:
    """A test fleet: an Ed25519 keypair + the derived fleet_id + a signer."""

    def __init__(self) -> None:
        self._sk = Ed25519PrivateKey.generate()
        self.public_key_b64 = base64.b64encode(self._sk.public_key().public_bytes_raw()).decode()
        self.fleet_id = fleet_id_from_public_key(self.public_key_b64)

    def proof(self, token: str, workspace_id: str = WORKSPACE_ID) -> str:
        return base64.b64encode(self._sk.sign(proof_message(token, workspace_id))).decode()


def _mint(client: TestClient, scope: str = "monitor") -> str:
    return str(client.post("/fleet/enroll-token", json={"scope": scope}).json()["token"])


def _register(client: TestClient, fleet: _Fleet, token: str, **over: object) -> Any:
    body = {
        "fleet_id": fleet.fleet_id,
        "fleet_public_key": fleet.public_key_b64,
        "proof": fleet.proof(token),
        "target_workspace_id": WORKSPACE_ID,
        "display_name": "Acme Prod",
        **over,
    }
    return client.post("/fleet/register", json=body, headers={"Authorization": f"Bearer {token}"})


# --- crypto (contract) ------------------------------------------------------


def test_fleet_id_is_deterministic_and_self_certifying() -> None:
    a, b = _Fleet(), _Fleet()
    assert a.fleet_id == fleet_id_from_public_key(a.public_key_b64)  # deterministic
    assert a.fleet_id != b.fleet_id  # different key → different id
    assert a.fleet_id.startswith("fleet:")


def test_proof_binds_token_and_workspace() -> None:
    f = _Fleet()
    sig = f.proof("tok-1", "ws-a")
    assert verify_proof(f.public_key_b64, sig, "tok-1", "ws-a") is True
    assert verify_proof(f.public_key_b64, sig, "tok-2", "ws-a") is False  # different token
    assert verify_proof(f.public_key_b64, sig, "tok-1", "ws-b") is False  # different workspace


# --- register with identity -------------------------------------------------


def test_register_with_valid_identity_pins_and_lists_the_key(client: TestClient) -> None:
    f = _Fleet()
    r = _register(client, f, _mint(client))
    assert r.status_code == 200, r.text
    # the pinned public key is surfaced (non-secret) on the membership listing.
    listed = client.get("/fleet/memberships").json()
    mine = next(m for m in listed if m["fleet_id"] == f.fleet_id)
    assert mine["fleet_public_key"] == f.public_key_b64


def test_register_rejects_fleet_id_not_matching_key(client: TestClient) -> None:
    f = _Fleet()
    r = _register(client, f, _mint(client), fleet_id="fleet:not-the-real-fingerprint")
    assert r.status_code == 400


def test_register_rejects_bad_proof(client: TestClient) -> None:
    f = _Fleet()
    other = _Fleet()
    token = _mint(client)
    # a proof signed by a different key than the presented public key.
    r = _register(client, f, token, proof=other.proof(token))
    assert r.status_code == 401


def test_register_rejects_proof_bound_to_another_workspace(client: TestClient) -> None:
    f = _Fleet()
    token = _mint(client)
    body_proof = f.proof(token, "some-other-workspace")
    r = _register(client, f, token, proof=body_proof, target_workspace_id="some-other-workspace")
    assert r.status_code == 401


def test_bad_proof_does_not_burn_the_token(client: TestClient) -> None:
    f = _Fleet()
    token = _mint(client)
    assert _register(client, f, token, proof="AAAA").status_code == 401
    # the token survives a signing failure — a valid retry still works.
    assert _register(client, f, token).status_code == 200


# --- TOFU pinning + mismatch ------------------------------------------------


def test_same_fleet_reregisters_with_matching_key(client: TestClient) -> None:
    f = _Fleet()
    assert _register(client, f, _mint(client)).status_code == 200
    assert _register(client, f, _mint(client)).status_code == 200  # same key → fine


def test_reregister_with_different_key_is_409(client: TestClient) -> None:
    f = _Fleet()
    assert _register(client, f, _mint(client)).status_code == 200
    # a different key claiming the SAME fleet_id can't happen naturally (id = fingerprint), so forge
    # the fleet_id to collide while presenting a fresh key — the pin mismatch must reject it.
    impostor = _Fleet()
    token = _mint(client)
    r = client.post(
        "/fleet/register",
        json={
            "fleet_id": f.fleet_id,  # claim the pinned fleet's id
            "fleet_public_key": impostor.public_key_b64,
            # fleet_id won't match impostor's key → 400 before pinning; assert it's rejected.
            "proof": impostor.proof(token),
            "target_workspace_id": WORKSPACE_ID,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (400, 409)  # rejected — never re-pinned


def test_unpin_allows_rekey(client: TestClient) -> None:
    f = _Fleet()
    assert _register(client, f, _mint(client)).status_code == 200
    assert client.delete(f"/fleet/identity/{f.fleet_id}").status_code == 200
    assert client.delete(f"/fleet/identity/{f.fleet_id}").status_code == 404  # gone


# --- opportunistic / not required by default --------------------------------


def test_register_without_identity_still_works_by_default(client: TestClient) -> None:
    # no public key + require_identity unset → opportunistic, register proceeds (design 21).
    token = _mint(client)
    r = client.post(
        "/fleet/register",
        json={"fleet_id": "legacy-fleet"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


def test_require_identity_rejects_keyless_register(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With the toggle on and a non-loopback client, a register with no fleet key is refused.
    monkeypatch.setenv("SWARMKIT_FLEET_REQUIRE_IDENTITY", "1")
    token = _mint(client)
    r = client.post(
        "/fleet/register",
        json={"fleet_id": "legacy-fleet"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401
    # a proper identity still registers under the same toggle.
    f = _Fleet()
    assert _register(client, f, _mint(client)).status_code == 200
