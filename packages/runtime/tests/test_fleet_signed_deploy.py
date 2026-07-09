"""Signed pushes (design 22): a deploy over a membership key must carry a valid fleet signature —
verified against the pinned key — so a stolen membership key alone can't push.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from swarmkit_runtime.fleet import deploy_message, fleet_id_from_public_key, proof_message
from swarmkit_runtime.server import create_app
from swarmkit_runtime.server._helpers import _content_hash

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
WORKSPACE_ID = "hello-swarm"
ADMIN_TOKEN = "admin-transport-token"  # test literal


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


@pytest.fixture()
def client() -> TestClient:  # type: ignore[misc]
    from swarmkit_runtime.auth import APIKeyAuthProvider  # noqa: PLC0415

    auth = APIKeyAuthProvider(
        keys=[{"key_ref": ADMIN_TOKEN, "client_id": "operator", "tier": "admin"}]
    )
    with TestClient(create_app(EXAMPLE_WS, auth_provider=auth)) as c:
        yield c


class _Fleet:
    def __init__(self) -> None:
        self._sk = Ed25519PrivateKey.generate()
        self.public_key_b64 = base64.b64encode(self._sk.public_key().public_bytes_raw()).decode()
        self.fleet_id = fleet_id_from_public_key(self.public_key_b64)

    def sign_deploy(self, kind: str, artifact_id: str, content: Any) -> str:
        msg = deploy_message(kind, artifact_id, _content_hash(content))
        return base64.b64encode(self._sk.sign(msg)).decode()

    def register(self, client: TestClient) -> str:
        """Enroll (manage) with identity so the key is pinned; return the membership key."""
        token = client.post(
            "/fleet/enroll-token", json={"scope": "manage"}, headers=_bearer(ADMIN_TOKEN)
        ).json()["token"]
        proof = base64.b64encode(self._sk.sign(proof_message(token, WORKSPACE_ID))).decode()
        body = client.post(
            "/fleet/register",
            json={
                "fleet_id": self.fleet_id,
                "fleet_public_key": self.public_key_b64,
                "proof": proof,
                "target_workspace_id": WORKSPACE_ID,
            },
            headers=_bearer(token),
        ).json()
        return str(body["credential"]["value"])


def _bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


# a valid hello-swarm topology dict to deploy.
_CONTENT = {
    "apiVersion": "swarmkit/v1",
    "kind": "Topology",
    "metadata": {"id": "hello", "name": "hello", "version": "2.0.0"},
    "root": "root",
    "agents": [{"id": "root", "archetype": "hello-responder"}],
}


def _deploy(client: TestClient, key: str, content: Any, signature: str | None) -> int:
    headers = _bearer(key)
    if signature is not None:
        headers["X-Fleet-Signature"] = signature
    return client.put(
        "/api/topologies/hello", json={"content": content, "dry_run": True}, headers=headers
    ).status_code


def test_valid_fleet_signature_is_accepted(client: TestClient) -> None:
    fleet = _Fleet()
    key = fleet.register(client)
    sig = fleet.sign_deploy("topology", "hello", _CONTENT)
    # a manage membership + a valid signature over the content → applied (not an auth error).
    assert _deploy(client, key, _CONTENT, sig) not in (401, 403)


def test_invalid_signature_is_rejected_even_when_not_required(client: TestClient) -> None:
    fleet = _Fleet()
    key = fleet.register(client)
    # a signature by a DIFFERENT key than the pinned one — rejected, opt-in or not.
    other = _Fleet()
    bad = other.sign_deploy("topology", "hello", _CONTENT)
    assert _deploy(client, key, _CONTENT, bad) == 401


def test_signature_over_different_content_is_rejected(client: TestClient) -> None:
    fleet = _Fleet()
    key = fleet.register(client)
    # sign the real content but deploy tampered content → hash mismatch → rejected.
    sig = fleet.sign_deploy("topology", "hello", _CONTENT)
    tampered = {**_CONTENT, "agents": [{"id": "root", "archetype": "evil"}]}
    assert _deploy(client, key, tampered, sig) == 401


def test_unsigned_deploy_allowed_by_default_rejected_when_required(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet = _Fleet()
    key = fleet.register(client)
    # default (no require flag): an unsigned manage deploy is allowed (opportunistic).
    assert _deploy(client, key, _CONTENT, None) not in (401, 403)
    # with signing required, the same unsigned deploy is refused.
    monkeypatch.setenv("SWARMKIT_FLEET_REQUIRE_SIGNED_DEPLOY", "1")
    assert _deploy(client, key, _CONTENT, None) == 401
    # a valid signature still passes under the requirement.
    sig = fleet.sign_deploy("topology", "hello", _CONTENT)
    assert _deploy(client, key, _CONTENT, sig) not in (401, 403)


def test_signed_deploy_follows_require_identity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # require_signed_deploy defaults to require_identity when its own flag is unset.
    monkeypatch.setenv("SWARMKIT_FLEET_REQUIRE_IDENTITY", "1")
    fleet = _Fleet()
    key = fleet.register(client)
    assert _deploy(client, key, _CONTENT, None) == 401  # inherited requirement → unsigned refused


def test_operator_transport_deploy_needs_no_signature(client: TestClient) -> None:
    # an operator admin token (not a membership) deploys with {content} and no signature — allowed;
    # the signature gate only applies to fleet (membership) deploys.
    status = client.put(
        "/api/topologies/hello",
        json={"content": _CONTENT, "dry_run": True},
        headers=_bearer(ADMIN_TOKEN),
    ).status_code
    assert status not in (401, 403)


def test_yaml_body_still_works_for_operator_edits(client: TestClient) -> None:
    yaml_text = yaml.safe_dump(_CONTENT)
    status = client.put(
        "/api/topologies/hello",
        json={"yaml": yaml_text, "dry_run": True},
        headers=_bearer(ADMIN_TOKEN),
    ).status_code
    assert status not in (401, 403)


# --- crypto contract --------------------------------------------------------


def test_deploy_message_is_stable_and_bound() -> None:
    a = deploy_message("topology", "hello", "h1")
    assert a == b"deploy:topology:hello:h1"
    assert a != deploy_message("skill", "hello", "h1")  # kind bound
    assert a != deploy_message("topology", "other", "h1")  # id bound
    assert a != deploy_message("topology", "hello", "h2")  # hash bound


# --- monotonic downgrade guard (design 22) ----------------------------------


def _seq_deploy(client: TestClient, key: str, content: Any, fleet: _Fleet, seq: int) -> int:
    sig = base64.b64encode(
        fleet._sk.sign(deploy_message("topology", "hello", _content_hash(content), seq))
    ).decode()
    return client.put(
        "/api/topologies/hello",
        json={"content": content, "deploy_seq": seq, "dry_run": True},
        headers={"Authorization": f"Bearer {key}", "X-Fleet-Signature": sig},
    ).status_code


def test_newer_sequence_accepted_stale_rejected(client: TestClient) -> None:
    fleet = _Fleet()
    key = fleet.register(client)
    assert _seq_deploy(client, key, _CONTENT, fleet, 5) not in (401, 403, 409)  # first
    assert _seq_deploy(client, key, _CONTENT, fleet, 6) not in (401, 403, 409)  # advances
    # replaying the older validly-signed deploy (seq 5) over the newer state → 409 downgrade guard.
    assert _seq_deploy(client, key, _CONTENT, fleet, 5) == 409
    assert _seq_deploy(client, key, _CONTENT, fleet, 6) == 409  # equal is not newer either


def test_sequence_is_per_artifact(client: TestClient) -> None:
    # a sequence advanced for one artifact does not block a lower sequence for a different one.
    fleet = _Fleet()
    key = fleet.register(client)
    assert _seq_deploy(client, key, _CONTENT, fleet, 10) not in (401, 403, 409)
    skill = {"apiVersion": "swarmkit/v1", "kind": "Skill", "metadata": {"id": "s", "name": "s"}}
    sig = base64.b64encode(
        fleet._sk.sign(deploy_message("skill", "s", _content_hash(skill), 3))
    ).decode()
    status = client.put(
        "/api/skills/s",
        json={"content": skill, "deploy_seq": 3, "dry_run": True},
        headers={"Authorization": f"Bearer {key}", "X-Fleet-Signature": sig},
    ).status_code
    assert status != 409  # skill seq 3 is fine despite topology being at 10


def test_sequence_must_be_inside_the_signature(client: TestClient) -> None:
    # signing seq 5 but claiming seq 9 in the body → signature over the 5-part message won't verify.
    fleet = _Fleet()
    key = fleet.register(client)
    sig = base64.b64encode(
        fleet._sk.sign(deploy_message("topology", "hello", _content_hash(_CONTENT), 5))
    ).decode()
    status = client.put(
        "/api/topologies/hello",
        json={"content": _CONTENT, "deploy_seq": 9, "dry_run": True},
        headers={"Authorization": f"Bearer {key}", "X-Fleet-Signature": sig},
    ).status_code
    assert status == 401  # tampered seq → invalid signature
