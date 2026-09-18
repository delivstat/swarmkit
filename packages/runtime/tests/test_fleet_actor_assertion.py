"""A fleet with an ``approve-as`` membership resolves a role-task as the person it asserts.

design/details/control-plane/28-operator-identity-to-instance.md. The transport identity is the
enrolment key (alice here, who is a member of security-reviewer); the assertion names bob, who is
a member of release-manager. The role registry decides membership; the scope decides whether the
fleet's word about *who* is believed; the signature decides whether it is the fleet's word.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from conftest import copy_workspace
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from swarmkit_runtime.fleet import (
    actor_message,
    fleet_id_from_public_key,
    proof_message,
    scope_covers,
)
from swarmkit_runtime.review import FileReviewQueue, ReviewItem
from swarmkit_runtime.server import create_app

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO / "examples" / "hello-swarm" / "workspace"
WORKSPACE_ID = "hello-swarm"
ALICE_KEY = "alice-key"  # test literal
ADMIN_KEY = "admin-key"  # test literal

ROLES_YAML = """\
apiVersion: swarmkit/v1
kind: RoleRegistry
metadata:
  id: test-roles
  name: Test roles
roles:
  - id: security-reviewer
    members: [alice]
    scopes: [security:approve]
  - id: release-manager
    members: [bob]
    scopes: [security:approve]
"""


def _role_task(item_id: str, role: str) -> ReviewItem:
    return ReviewItem(
        id=item_id,
        topology_id="run-42",
        agent_id="design",
        skill_id="multi-party-approval",
        output={
            "gate_id": "run-42:design",
            "scope": "security:approve",
            "role": role,
            "rule_index": 0,
        },
        verdict={},
        reason=f"multi-party approval: {role} must approve security:approve",
        timestamp=datetime.now(tz=UTC),
    )


class _Fleet:
    def __init__(self) -> None:
        self._sk = Ed25519PrivateKey.generate()
        self.public_key_b64 = base64.b64encode(self._sk.public_key().public_bytes_raw()).decode()
        self.fleet_id = fleet_id_from_public_key(self.public_key_b64)

    def register(self, client: TestClient, scope: str) -> None:
        token = client.post(
            "/fleet/enroll-token", json={"scope": scope}, headers=_bearer(ADMIN_KEY)
        ).json()["token"]
        proof = base64.b64encode(self._sk.sign(proof_message(token, WORKSPACE_ID))).decode()
        r = client.post(
            "/fleet/register",
            json={
                "fleet_id": self.fleet_id,
                "fleet_public_key": self.public_key_b64,
                "proof": proof,
                "target_workspace_id": WORKSPACE_ID,
            },
            headers=_bearer(token),
        )
        assert r.status_code == 200, r.text

    def assert_actor(
        self, item_id: str, subject: str, issued_at: int | None = None
    ) -> dict[str, str]:
        issued = int(time.time()) if issued_at is None else issued_at
        sig = base64.b64encode(self._sk.sign(actor_message(item_id, subject, issued))).decode()
        return {
            "X-Fleet-Id": self.fleet_id,
            "X-Fleet-Actor": subject,
            "X-Fleet-Actor-Signature": sig,
            "X-Fleet-Actor-Issued": str(issued),
        }


def _bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    ws = copy_workspace(EXAMPLE_WS, tmp_path / "ws")
    # The audit assertion below reads the mock governance provider's events directly; a bound
    # decision skill wraps the provider, so memory-by-default's bindings are switched off here.
    (ws / "workspace.yaml").write_text(
        (ws / "workspace.yaml").read_text() + "\nmemory: {enabled: false}\n"
    )
    (ws / "roles").mkdir(exist_ok=True)
    (ws / "roles" / "test-roles.yaml").write_text(ROLES_YAML)
    queue = FileReviewQueue(ws)
    queue.submit(_role_task("mpa-run-42:design-0-security-reviewer", "security-reviewer"))
    queue.submit(_role_task("mpa-run-42:design-0-release-manager", "release-manager"))
    return ws


@pytest.fixture
def client(workspace: Path) -> Iterator[TestClient]:
    from swarmkit_runtime.auth import APIKeyAuthProvider  # noqa: PLC0415

    auth = APIKeyAuthProvider(
        keys=[
            {"key_ref": ALICE_KEY, "client_id": "alice", "tier": "run"},
            {"key_ref": ADMIN_KEY, "client_id": "ops", "tier": "admin"},
        ]
    )
    with TestClient(create_app(workspace, auth_provider=auth)) as c:
        yield c


BOB_TASK = "mpa-run-42:design-0-release-manager"


def _resolve(
    client: TestClient, item_id: str, extra: dict[str, str] | None = None
) -> httpx.Response:
    return client.post(
        f"/review/{item_id}/resolve",
        json={"outcome": "approve", "comment": "ok"},
        headers={**_bearer(ALICE_KEY), **(extra or {})},
    )


def test_scope_order() -> None:
    assert scope_covers("approve-as", "manage") and scope_covers("approve-as", "monitor")
    assert scope_covers("manage", "manage") and not scope_covers("manage", "approve-as")
    assert not scope_covers("monitor", "manage") and not scope_covers("nonsense", "monitor")


def test_actor_message_is_stable() -> None:
    assert actor_message("mpa-1", "alice", 1700000000) == b"actor:mpa-1:alice:1700000000"


def test_without_an_assertion_the_transport_identity_resolves(client: TestClient) -> None:
    # alice's key, bob's task: alice is not a release-manager → refused, as before.
    r = _resolve(client, BOB_TASK)
    assert r.status_code == 403
    assert "alice may not resolve" in r.json()["detail"]


def test_an_approve_as_fleet_resolves_as_the_asserted_person(
    client: TestClient, workspace: Path
) -> None:
    fleet = _Fleet()
    fleet.register(client, "approve-as")
    r = _resolve(client, BOB_TASK, fleet.assert_actor(BOB_TASK, "bob"))
    assert r.status_code == 200, r.text
    assert r.json()["resolved_by"] == "bob"
    # The queue counts the resolution under bob, not under the enrolment key.
    items = {i["id"]: i for i in client.get("/review/all", headers=_bearer(ALICE_KEY)).json()}
    assert items[BOB_TASK]["status"] == "approved"
    assert items[BOB_TASK]["resolved_by"] == "bob"
    # Both "who" and "through what" are on the audit (the mock governance provider keeps its
    # events in memory; the audit store is what a real provider writes through).
    gov = client.app.state.runtime.governance  # type: ignore[attr-defined]
    resolved = [e for e in gov._events if e.event_type == "approval.role_task_resolved"]
    assert resolved[0].payload["identity"] == "bob"
    assert resolved[0].payload["via_fleet"] == fleet.fleet_id
    assert resolved[0].agent_id == "bob"


def test_the_role_registry_still_decides(client: TestClient) -> None:
    """approve-as means 'believe the panel about who' — not 'anyone the panel names may approve'."""
    fleet = _Fleet()
    fleet.register(client, "approve-as")
    r = _resolve(client, BOB_TASK, fleet.assert_actor(BOB_TASK, "mallory"))
    assert r.status_code == 403
    assert "mallory is not a member of role release-manager" in r.json()["detail"]


def test_a_manage_fleet_may_not_assert(client: TestClient) -> None:
    fleet = _Fleet()
    fleet.register(client, "manage")
    r = _resolve(client, BOB_TASK, fleet.assert_actor(BOB_TASK, "bob"))
    assert r.status_code == 401
    assert "not granted approve-as" in r.json()["detail"]


def test_a_bad_signature_a_stale_assertion_and_an_unknown_fleet_are_refused(
    client: TestClient,
) -> None:
    fleet = _Fleet()
    fleet.register(client, "approve-as")
    # signed for a different item
    headers = fleet.assert_actor("mpa-other", "bob")
    assert _resolve(client, BOB_TASK, headers).status_code == 401
    # stale
    headers = fleet.assert_actor(BOB_TASK, "bob", issued_at=int(time.time()) - 3600)
    r = _resolve(client, BOB_TASK, headers)
    assert r.status_code == 401 and "stale" in r.json()["detail"]
    # a fleet this instance never registered
    stranger = _Fleet()
    r = _resolve(client, BOB_TASK, stranger.assert_actor(BOB_TASK, "bob"))
    assert r.status_code == 401 and "no membership" in r.json()["detail"]
    # and after all that, bob's task is still pending
    pending = client.get("/review", headers=_bearer(ALICE_KEY)).json()
    assert BOB_TASK in {i["id"] for i in pending}


def test_enroll_token_accepts_approve_as(client: TestClient) -> None:
    r = client.post("/fleet/enroll-token", json={"scope": "approve-as"}, headers=_bearer(ADMIN_KEY))
    assert r.status_code == 200, r.text
    assert (
        client.post(
            "/fleet/enroll-token", json={"scope": "nonsense"}, headers=_bearer(ADMIN_KEY)
        ).status_code
        == 400
    )
