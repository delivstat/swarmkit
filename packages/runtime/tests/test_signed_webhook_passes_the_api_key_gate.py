"""A signed webhook reaches its trigger's own check on an API-key-protected serve, and the
trigger's `credentials_ref` names a workspace credential.

Two defects, one route. The auth middleware demanded a bearer token on `/hooks/{topology}` — which
GitHub cannot send — so turning `server.auth` on silenced every webhook trigger with a 401 before
its signature was read. And `credentials_ref` was read as an environment-variable NAME rather
than a `credentials` entry, so a trigger written the documented way was refused with 503. A
webhook trigger with NO auth block stays behind the gate: enabling auth must not open a door.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.auth import APIKeyAuthProvider

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: w, name: W}
governance: {provider: mock}
credentials:
  github-webhook-secret:
    source: env
    config: {env: GITHUB_WEBHOOK_SECRET}
"""
_TOPO = """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: %s, version: 0.1.0}
agents:
  root:
    id: greeter
    role: root
    model: {provider: mock, name: m}
    prompt: {system: greet}
"""
_SIGNED = """apiVersion: swarmkit/v1
kind: Trigger
metadata: {id: pr-opened, name: PR opened}
type: webhook
targets: [hello]
config:
  auth: {method: hmac, credentials_ref: github-webhook-secret}
"""
_UNSIGNED = """apiVersion: swarmkit/v1
kind: Trigger
metadata: {id: anything, name: Anything}
type: webhook
targets: [open]
config: {}
"""


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:  # type: ignore[misc]
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    (tmp_path / "topologies").mkdir()
    (tmp_path / "triggers").mkdir()
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "topologies" / "hello.yaml").write_text(_TOPO % "hello")
    (tmp_path / "topologies" / "open.yaml").write_text(_TOPO % "open")
    (tmp_path / "triggers" / "pr-opened.yaml").write_text(_SIGNED)
    (tmp_path / "triggers" / "anything.yaml").write_text(_UNSIGNED)
    provider = APIKeyAuthProvider(keys=[{"key_ref": "secret", "client_id": "cp", "tier": "run"}])
    with TestClient(create_app(tmp_path, auth_provider=provider)) as c:
        yield c


def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()


def test_a_signed_delivery_starts_the_run(client: TestClient) -> None:
    body = b'{"action": "opened"}'
    res = client.post(
        "/hooks/hello",
        content=body,
        headers={"content-type": "application/json", "X-Hub-Signature-256": _sig(body)},
    )
    # On the unfixed code: 401 from the API-key gate (or 503 once past it, the ref unresolved).
    assert res.status_code == 200, res.text
    assert res.json()["topology"] == "hello"


def test_a_wrong_signature_is_refused(client: TestClient) -> None:
    body = b'{"action": "opened"}'
    res = client.post(
        "/hooks/hello",
        content=body,
        headers={"content-type": "application/json", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert res.status_code == 401


def test_a_webhook_without_its_own_auth_stays_behind_the_gate(client: TestClient) -> None:
    res = client.post("/hooks/open", json={"input": "x"})
    assert res.status_code == 401
    res = client.post(
        "/hooks/open", json={"input": "x"}, headers={"Authorization": "Bearer secret"}
    )
    assert res.status_code == 200


_BEARER = """apiVersion: swarmkit/v1
kind: Trigger
metadata: {id: ci-done, name: CI done}
type: webhook
targets: [open]
config:
  auth: {method: bearer, credentials_ref: github-webhook-secret}
"""


def test_a_bearer_webhook_is_checked_as_a_bearer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The schema offered `bearer` and `api_key` and the runtime verified every method as an
    HMAC signature, so a bearer trigger refused every delivery."""
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    (tmp_path / "topologies").mkdir()
    (tmp_path / "triggers").mkdir()
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "topologies" / "open.yaml").write_text(_TOPO % "open")
    (tmp_path / "triggers" / "ci-done.yaml").write_text(_BEARER)
    with TestClient(create_app(tmp_path)) as client:
        ok = client.post(
            "/hooks/open", json={"input": "x"}, headers={"Authorization": "Bearer s3cret"}
        )
        assert ok.status_code == 200, ok.text
        bad = client.post(
            "/hooks/open", json={"input": "x"}, headers={"Authorization": "Bearer no"}
        )
        assert bad.status_code == 401
