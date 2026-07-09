"""Demo: signed pushes (design 22) — a stolen membership key alone can't deploy.

Against a live serve (TestClient): a fleet enrolls (its key is pinned), then deploys a topology.
A valid fleet signature is applied; a tampered payload (hash mismatch) is rejected; and under
``require_signed_deploy`` an unsigned deploy is refused while a signed one passes.

Usage:  uv run python scripts/demo_signed_deploy.py
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from swarmkit_runtime.auth import APIKeyAuthProvider
from swarmkit_runtime.fleet import deploy_message, fleet_id_from_public_key, proof_message
from swarmkit_runtime.server import create_app
from swarmkit_runtime.server._helpers import _content_hash

WS = Path(__file__).resolve().parent.parent / "examples" / "hello-swarm" / "workspace"
WORKSPACE_ID = "hello-swarm"
ADMIN = "admin-token"

CONTENT = {
    "apiVersion": "swarmkit/v1",
    "kind": "Topology",
    "metadata": {"id": "hello", "name": "hello", "version": "2.0.0"},
    "root": "root",
    "agents": [{"id": "root", "archetype": "hello-responder"}],
}


def main() -> int:
    os.environ.setdefault("SWARMKIT_PROVIDER", "mock")
    auth = APIKeyAuthProvider(keys=[{"key_ref": ADMIN, "client_id": "op", "tier": "admin"}])
    admin = {"Authorization": f"Bearer {ADMIN}"}
    with TestClient(create_app(WS, auth_provider=auth)) as client:
        sk = Ed25519PrivateKey.generate()
        pub = base64.b64encode(sk.public_key().public_bytes_raw()).decode()
        fleet_id = fleet_id_from_public_key(pub)

        # enrol with identity so the key is pinned, and get the manage membership key.
        token = client.post("/fleet/enroll-token", json={"scope": "manage"}, headers=admin).json()[
            "token"
        ]
        proof = base64.b64encode(sk.sign(proof_message(token, WORKSPACE_ID))).decode()
        key = client.post(
            "/fleet/register",
            json={
                "fleet_id": fleet_id,
                "fleet_public_key": pub,
                "proof": proof,
                "target_workspace_id": WORKSPACE_ID,
            },
            headers={"Authorization": f"Bearer {token}"},
        ).json()["credential"]["value"]
        print(f"fleet {fleet_id} enrolled (key pinned)")

        def deploy(content: dict, sign_content: dict | None, seq: int | None = None) -> int:
            headers = {"Authorization": f"Bearer {key}"}
            body: dict = {"content": content, "dry_run": True}
            if seq is not None:
                body["deploy_seq"] = seq
            if sign_content is not None:
                msg = deploy_message("topology", "hello", _content_hash(sign_content), seq)
                headers["X-Fleet-Signature"] = base64.b64encode(sk.sign(msg)).decode()
            return client.put("/api/topologies/hello", json=body, headers=headers).status_code

        print(f"  deploy with a valid signature      -> {deploy(CONTENT, CONTENT)} (applied)")
        tampered = {**CONTENT, "agents": [{"id": "root", "archetype": "evil"}]}
        print(f"  deploy tampered content (same sig) -> {deploy(tampered, CONTENT)} (rejected)")
        print(f"  unsigned deploy (default)          -> {deploy(CONTENT, None)} (allowed)")

        os.environ["SWARMKIT_FLEET_REQUIRE_SIGNED_DEPLOY"] = "1"
        print(f"  unsigned deploy (required)         -> {deploy(CONTENT, None)} (rejected)")
        print(f"  signed deploy (required)           -> {deploy(CONTENT, CONTENT)} (applied)")

        print("\nmonotonic downgrade guard (design 22):")
        print(f"  signed deploy seq=5                -> {deploy(CONTENT, CONTENT, 5)} (applied)")
        print(f"  signed deploy seq=6                -> {deploy(CONTENT, CONTENT, 6)} (applied)")
        print(f"  replay old signed deploy seq=5     -> {deploy(CONTENT, CONTENT, 5)} (rejected)")

    print(
        "\nOK — a valid fleet signature is required to push, and an old signed deploy can't be "
        "replayed over a newer one."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
