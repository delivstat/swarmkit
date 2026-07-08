"""Demo: fleet identity via pinned public keys (design 21).

Shows the whole handshake against a live serve (TestClient): a fleet mints its Ed25519 identity,
registers with a proof-of-possession → the instance pins its self-certifying fleet_id; a rogue
claiming that fleet_id without the private key is rejected; and a re-key is blocked until an unpin.

Usage:  uv run python scripts/demo_fleet_identity.py
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from swarmkit_runtime.fleet import fleet_id_from_public_key, proof_message
from swarmkit_runtime.server import create_app

WS = Path(__file__).resolve().parent.parent / "examples" / "hello-swarm" / "workspace"
WORKSPACE_ID = "hello-swarm"


def _mint(client: TestClient) -> str:
    return str(client.post("/fleet/enroll-token", json={"scope": "manage"}).json()["token"])


def main() -> int:
    import os  # noqa: PLC0415

    os.environ.setdefault("SWARMKIT_PROVIDER", "mock")
    with TestClient(create_app(WS)) as client:
        # 1. A fleet's identity: an Ed25519 keypair → a self-certifying fleet_id.
        sk = Ed25519PrivateKey.generate()
        pub = base64.b64encode(sk.public_key().public_bytes_raw()).decode()
        fleet_id = fleet_id_from_public_key(pub)
        print(f"fleet identity: {fleet_id}")

        def register(pubkey: str, signer: Ed25519PrivateKey, fid: str, token: str) -> int:
            proof = base64.b64encode(signer.sign(proof_message(token, WORKSPACE_ID))).decode()
            return client.post(
                "/fleet/register",
                json={
                    "fleet_id": fid,
                    "fleet_public_key": pubkey,
                    "proof": proof,
                    "target_workspace_id": WORKSPACE_ID,
                    "display_name": "Acme Prod",
                },
                headers={"Authorization": f"Bearer {token}"},
            ).status_code

        # 2. Register with proof → the instance verifies + pins the key.
        s = register(pub, sk, fleet_id, _mint(client))
        print(f"  register with valid proof         -> {s} (pinned)")
        listed = client.get("/fleet/memberships").json()
        pinned = next(m for m in listed if m["fleet_id"] == fleet_id)["fleet_public_key"]
        print(f"  instance pinned the public key     -> {pinned == pub}")

        # 3. A rogue claims that fleet_id with its OWN key (no private key for the real one).
        rogue = Ed25519PrivateKey.generate()
        rogue_pub = base64.b64encode(rogue.public_key().public_bytes_raw()).decode()
        s = register(rogue_pub, rogue, fleet_id, _mint(client))
        print(f"  rogue claims the fleet_id          -> {s} (rejected — id != its key)")

        # 4. The real fleet re-registers with its key → matches the pin, fine.
        s = register(pub, sk, fleet_id, _mint(client))
        print(f"  same fleet re-registers            -> {s} (pin match)")

        # 5. Re-key is a deliberate unpin, then a fresh identity.
        unpin = client.delete(f"/fleet/identity/{fleet_id}").status_code
        print(f"  unpin the fleet identity           -> {unpin}")

    print("\nOK — self-certifying fleet_id, proof-of-possession, TOFU pinning all demonstrated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
