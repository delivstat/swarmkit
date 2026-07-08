"""Panel fleet identity (design 21): the Ed25519 keypair, encrypted private key, self-certifying
fleet_id, proof signing, GET /fleet/identity, register attaching the proof — and the cross-package
contract that the panel and serve derive the same fleet_id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._fleet_identity import FleetIdentity, fleet_id_from_public_key
from swarmkit_control_plane._secret_box import FernetSecretBox
from swarmkit_control_plane._tables import fleet_identity as _identity_table

# --- FleetIdentity ----------------------------------------------------------


def test_identity_generates_persists_and_encrypts(tmp_path: Path) -> None:
    box = FernetSecretBox(Fernet.generate_key())
    db = tmp_path / "cp.sqlite"
    idn = FleetIdentity(db, secret_box=box, display_name="Acme")
    assert idn.fleet_id.startswith("fleet:")
    assert idn.fleet_id == fleet_id_from_public_key(idn.public_key_b64)  # self-certifying
    assert idn.display_name == "Acme"

    # the private key is encrypted at rest (the raw row never holds it plainly).
    from sqlalchemy import select  # noqa: PLC0415

    with idn.engine.connect() as conn:
        row = conn.execute(select(_identity_table)).mappings().first()
    assert row is not None
    assert idn.public_key_b64 not in row["private_key_ciphertext"]

    # reopening loads the SAME identity (private key decrypts + roundtrips).
    idn2 = FleetIdentity(db, secret_box=box)
    assert idn2.fleet_id == idn.fleet_id


def test_proof_verifies_on_the_serve_side(tmp_path: Path) -> None:
    # the panel signs; serve's verifier accepts it — proving the two implementations agree.
    from swarmkit_runtime.fleet import verify_proof  # noqa: PLC0415

    idn = FleetIdentity(tmp_path / "cp.sqlite", secret_box=FernetSecretBox(Fernet.generate_key()))
    proof = idn.sign_proof("tok-abc", "ws-oms")
    assert verify_proof(idn.public_key_b64, proof, "tok-abc", "ws-oms") is True
    assert verify_proof(idn.public_key_b64, proof, "tok-abc", "other-ws") is False


def test_cross_package_fleet_id_derivation_matches() -> None:
    # Panel and serve MUST derive the same fleet_id from the same public key (design 21 contract).
    from swarmkit_runtime.fleet import fleet_id_from_public_key as serve_derive  # noqa: PLC0415

    idn = FleetIdentity(":memory:", secret_box=FernetSecretBox(Fernet.generate_key()))
    assert serve_derive(idn.public_key_b64) == idn.fleet_id


# --- routes -----------------------------------------------------------------


def _client(tmp_path: Path, register_calls: list[dict[str, Any]] | None = None) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    async def register_fn(
        endpoint: str,
        enroll_token: str,
        fleet_id: str,
        requested_scope: str | None,
        **identity: Any,
    ) -> dict[str, Any]:
        if register_calls is not None:
            register_calls.append({"fleet_id": fleet_id, **identity})
        return {
            "membership_id": "mem-1",
            "credential": {
                "type": "api_key",
                "value": "k",
                "scope": "monitor",
                "fingerprint": "fp",
            },
            "instance_state": {"schema_version": "", "artifacts": {}},
        }

    return TestClient(create_app(registry, verify=verify, register_fn=register_fn))


def test_get_fleet_identity_exposes_no_private_key(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/fleet/identity").json()
    assert body["fleet_id"].startswith("fleet:")
    assert body["fleet_public_key"]
    assert "private" not in str(body).lower()


def test_register_attaches_fleet_public_key_and_proof(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    client = _client(tmp_path, register_calls=calls)
    panel_fleet_id = client.get("/fleet/identity").json()["fleet_id"]
    iid = client.post(
        "/instances",
        json={"name": "dc", "endpoint": "http://serve:8000", "connection": "direct"},
    ).json()["id"]

    assert client.post(f"/instances/{iid}/register", json={"enroll_token": "t"}).status_code == 200
    # the panel signed with its self-certifying identity and passed the proof through.
    assert len(calls) == 1
    call = calls[0]
    assert call["fleet_id"] == panel_fleet_id  # not an operator string — the identity's id
    assert call["fleet_public_key"] and call["proof"]
    assert "target_workspace_id" in call
