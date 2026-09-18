"""Federated harness gates (GET/POST /instances/{id}/review) — the fleet operator's view of §6.2
permission + §6.3 input gates paused on instances, resolved through the same /review API the CLI +
serve UI use. Live-pulled (Mode A / direct); a NAT'd Mode-B instance can't be federated inbound.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._connector import ConnectorError, GateRefused

_GATES: list[dict[str, Any]] = [
    {"id": "approval-1", "kind": "permission", "agent_id": "coder", "capability": "Bash(npm test)"},
    {
        "id": "input-1",
        "kind": "input",
        "agent_id": "coder",
        "question": "Which cache?",
        "options": ["redis", "memcached"],
    },
]


def _client(tmp_path: Path, gates_fn: Any = None, resolve_fn: Any = None) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {}

    if gates_fn is None:

        async def gates_fn(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
            return _GATES

    if resolve_fn is None:

        async def resolve_fn(
            endpoint: str,
            token_ref: str,
            item_id: str,
            action: str,
            answer: str = "",
            *,
            outcome: str = "",
            comment: str = "",
        ) -> dict[str, Any]:
            return {"id": item_id, "status": "approved", "answer": answer}

    return TestClient(create_app(registry, verify=verify, gates=gates_fn, resolve_gate=resolve_fn))


def _enroll(client: TestClient, connection: str) -> str:
    return str(
        client.post(
            "/instances",
            json={"name": "x", "endpoint": "http://serve:8000", "connection": connection},
        ).json()["id"]
    )


def test_direct_instance_surfaces_pending_gates(tmp_path: Path) -> None:
    seen: list[str] = []

    async def gates_fn(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        seen.append(endpoint)
        return _GATES

    client = _client(tmp_path, gates_fn)
    iid = _enroll(client, "direct")
    body = client.get(f"/instances/{iid}/review").json()

    assert body["reachable"] is True and body["reason"] is None
    assert [g["id"] for g in body["gates"]] == ["approval-1", "input-1"]
    assert seen == ["http://serve:8000"]  # pulled live, never stored


def test_poll_mode_instance_cannot_be_federated(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client, "poll")
    resp = client.get(f"/instances/{iid}/review")
    assert resp.status_code == 200
    body = resp.json()
    assert (body["reachable"], body["reason"], body["gates"]) == (False, "poll-mode", [])
    assert body["resolves_as"]["kind"] == "instance-key"


def test_unreachable_instance_flips_health(tmp_path: Path) -> None:
    async def boom(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        raise ConnectorError("connection refused")

    client = _client(tmp_path, boom)
    iid = _enroll(client, "direct")
    assert client.get(f"/instances/{iid}/review").json()["reachable"] is False
    assert client.get(f"/instances/{iid}").json()["health"] == "unreachable"


def test_resolve_proxies_the_decision_to_the_instance(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []

    async def resolve_fn(
        endpoint: str,
        token_ref: str,
        item_id: str,
        action: str,
        answer: str = "",
        *,
        outcome: str = "",
        comment: str = "",
        actor: Any = None,
    ) -> dict[str, Any]:
        calls.append((item_id, action, answer))
        return {"id": item_id, "status": "approved", "answer": answer}

    client = _client(tmp_path, resolve_fn=resolve_fn)
    iid = _enroll(client, "direct")

    client.post(f"/instances/{iid}/review/approval-1/approve", json={})
    client.post(f"/instances/{iid}/review/input-1/answer", json={"answer": "redis"})

    assert ("approval-1", "approve", "") in calls
    assert ("input-1", "answer", "redis") in calls


def test_resolve_rejects_bad_action_and_poll_mode(tmp_path: Path) -> None:
    client = _client(tmp_path)
    direct = _enroll(client, "direct")
    assert client.post(f"/instances/{direct}/review/x/nope", json={}).status_code == 400
    poll = _enroll(client, "poll")
    assert client.post(f"/instances/{poll}/review/x/approve", json={}).status_code == 409


def test_unknown_instance_404(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/instances/nope/review").status_code == 404


def test_resolve_forwards_a_multi_party_outcome(tmp_path: Path) -> None:
    """A funnel's role-task is resolved with the `resolve` verb and an outcome — the generic
    approve marks the queue row without counting toward the gate (the runtime now refuses it)."""
    seen: list[dict[str, Any]] = []

    async def resolve_fn(
        endpoint: str,
        token_ref: str,
        item_id: str,
        action: str,
        answer: str = "",
        *,
        outcome: str = "",
        comment: str = "",
        actor: Any = None,
    ) -> dict[str, Any]:
        seen.append({"item": item_id, "action": action, "outcome": outcome, "comment": comment})
        return {"id": item_id, "kind": "role_task", "status": "approved", "resolved_by": "panel"}

    client = _client(tmp_path, resolve_fn=resolve_fn)
    iid = _enroll(client, "direct")
    r = client.post(
        f"/instances/{iid}/review/mpa-run:design-0-lead/resolve",
        json={"outcome": "changes-requested", "comment": "tighten the scope"},
    )
    assert r.status_code == 200, r.text
    assert seen == [
        {
            "item": "mpa-run:design-0-lead",
            "action": "resolve",
            "outcome": "changes-requested",
            "comment": "tighten the scope",
        }
    ]
    bad = client.post(f"/instances/{iid}/review/mpa-run:design-0-lead/resolve", json={})
    assert bad.status_code == 400
    assert "outcome" in bad.json()["detail"]


def test_an_instance_refusal_is_relayed_not_reported_as_unreachable(tmp_path: Path) -> None:
    """The instance said no (not a member of the role, wrong verb for the kind): the operator gets
    the instance's reason with its status, and the instance is NOT marked unreachable."""

    async def resolve_fn(
        endpoint: str,
        token_ref: str,
        item_id: str,
        action: str,
        answer: str = "",
        *,
        outcome: str = "",
        comment: str = "",
        actor: Any = None,
    ) -> dict[str, Any]:
        raise GateRefused(403, "panel is not a member of role security-reviewer")

    client = _client(tmp_path, resolve_fn=resolve_fn)
    iid = _enroll(client, "direct")
    r = client.post(f"/instances/{iid}/review/mpa-1/resolve", json={"outcome": "approve"})
    assert r.status_code == 403
    assert "not a member" in r.json()["detail"]
    assert client.get(f"/instances/{iid}").json()["health"] != "unreachable"


def test_resolves_as_the_signed_in_operator_when_the_panel_can_vouch(tmp_path: Path) -> None:
    """With an OIDC operator, a fleet identity and a membership on the instance, a resolve carries
    a signed assertion of the operator's subject that verifies against the panel's public key
    (design 28); the listing says so up front. Without a subject, no assertion travels."""
    import base64  # noqa: PLC0415

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
        Ed25519PublicKey,
    )
    from swarmkit_control_plane._auth import Principal  # noqa: PLC0415
    from swarmkit_control_plane._fleet_identity import actor_message  # noqa: PLC0415

    seen: list[Any] = []

    async def resolve_fn(
        endpoint: str,
        token_ref: str,
        item_id: str,
        action: str,
        answer: str = "",
        *,
        outcome: str = "",
        comment: str = "",
        actor: Any = None,
    ) -> dict[str, Any]:
        seen.append(actor)
        return {"id": item_id, "status": "approved"}

    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {}

    async def gates_fn(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        return _GATES

    app = create_app(registry, verify=verify, gates=gates_fn, resolve_gate=resolve_fn)

    # Stand in for the OIDC middleware: every request is alice.
    @app.middleware("http")
    async def _as_alice(request: Any, call_next: Any) -> Any:
        request.state.principal = Principal("operator", subject="alice")
        return await call_next(request)

    client = TestClient(app)
    iid = _enroll(client, "direct")
    # No membership yet → the enrolment key, with the reason.
    who = client.get(f"/instances/{iid}/review").json()["resolves_as"]
    assert who["kind"] == "instance-key" and "membership" in who["reason"]
    client.post(f"/instances/{iid}/review/mpa-1/resolve", json={"outcome": "approve"})
    assert seen == [None]
    # A membership the instance issued (so it pinned our key) → alice, signed.
    app.state.cred_store.put_credential(
        iid, membership_id="m1", fleet_id="f", scope="approve-as", fingerprint="ab", secret="k"
    )
    who = client.get(f"/instances/{iid}/review").json()["resolves_as"]
    assert who == {"kind": "subject", "subject": "alice"}
    client.post(f"/instances/{iid}/review/mpa-1/resolve", json={"outcome": "approve"})
    assertion = seen[-1]
    assert assertion.subject == "alice" and assertion.fleet_id == app.state.fleet_identity.fleet_id
    pub = Ed25519PublicKey.from_public_bytes(
        base64.b64decode(app.state.fleet_identity.public_key_b64)
    )
    pub.verify(
        base64.b64decode(assertion.signature),
        actor_message("mpa-1", "alice", assertion.issued_at),
    )
