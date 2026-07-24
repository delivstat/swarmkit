"""Tests for the pipeline ingress front door + governance guardrail (37b).

Covers the runtime-side, domain-neutral ingress of design/details/pipeline-triggering.md
§"The governance guardrail":

- the reserved scopes ``pipeline:advance`` / ``pipeline:skip`` are un-grantable to a transport token
  (a transport api-key/JWT can never carry them);
- ``POST /pipelines/signal`` mode=emit delivers to an injected ``PipelineSignal`` sink;
- mode=skip WITHOUT the reserved scope is a 403 *and* is audited (the denial is recorded);
- mode=skip WITH the scope (governance decision mocked allowed) is delivered *and* audited;
- an unset sink is a sanctioned 503.

The receiver (webhook Trigger-target) and the chat interpreter are 37c and are not exercised here.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.auth import reserved_violations
from swarmkit_runtime.auth._api_key import APIKeyAuthProvider
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.governance._mock import MockGovernanceProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


# ---- (a) reserved scopes: un-grantable to a transport token ------------------


def test_reserved_violations_flags_pipeline_operator_scopes() -> None:
    assert "pipeline:skip" in reserved_violations(frozenset({"pipeline:skip"}))
    assert "pipeline:advance" in reserved_violations(frozenset({"pipeline:advance"}))
    # a plain transport scope is not a violation
    assert reserved_violations(frozenset({"serve:run"})) == frozenset()


def test_api_key_carrying_pipeline_skip_is_rejected() -> None:
    """A transport token can never carry ``pipeline:skip`` — the provider refuses to load it."""
    with pytest.raises(ValueError, match="reserved governance scope"):
        APIKeyAuthProvider(
            keys=[
                {
                    "key_ref": "secret-token",
                    "client_id": "ci-bot",
                    "scopes": ["serve:run", "pipeline:skip"],
                }
            ]
        )


# ---- serve endpoint fixtures -------------------------------------------------


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    dest = tmp_path / "workspace"
    shutil.copytree(EXAMPLE_WS, dest)
    return dest


@pytest.fixture()
def client(ws: Path) -> Iterator[TestClient]:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws)
    with TestClient(app) as c:
        yield c


def _install_governance(client: TestClient, gov: MockGovernanceProvider) -> None:
    """Swap the loaded runtime's governance provider so a test can control the guardrail decision
    and read back the audit trail it records."""
    client.app.state.runtime._governance = gov  # type: ignore[attr-defined]


def _ingress_events(gov: MockGovernanceProvider) -> list[AuditEvent]:
    return [e for e in gov.events if e.event_type == "pipeline.ingress"]


# ---- (b) mode=emit delivers to the injected sink -----------------------------


def test_signal_emit_delivers_to_injected_sink(client: TestClient) -> None:
    gov = MockGovernanceProvider()  # emit never consults evaluate_action
    _install_governance(client, gov)
    calls: list[tuple[str, str]] = []

    async def fake_signal(correlation_id: str, event: str) -> None:
        calls.append((correlation_id, event))

    client.app.state.pipeline_signal = fake_signal  # type: ignore[attr-defined]
    resp = client.post(
        "/pipelines/signal",
        json={"correlation_id": "CORR-1", "event": "build.ready-in-qa", "mode": "emit"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered"] is True
    assert body == {
        "delivered": True,
        "correlation_id": "CORR-1",
        "event": "build.ready-in-qa",
        "mode": "emit",
        "source": body["source"],
    }
    assert calls == [("CORR-1", "build.ready-in-qa")]
    # emit is still audited (every ingress event is recorded)
    events = _ingress_events(gov)
    assert len(events) == 1
    assert events[0].payload["mode"] == "emit"
    assert events[0].payload["allowed"] is True


# ---- (c) mode=skip WITHOUT the scope → 403 AND audited -----------------------


def test_signal_skip_without_scope_is_denied_and_audited(client: TestClient) -> None:
    # governance grants no scopes → the skip authorization is denied
    gov = MockGovernanceProvider(allowed_scopes=frozenset())
    _install_governance(client, gov)
    delivered: list[tuple[str, str]] = []

    async def fake_signal(correlation_id: str, event: str) -> None:
        delivered.append((correlation_id, event))

    client.app.state.pipeline_signal = fake_signal  # type: ignore[attr-defined]
    resp = client.post(
        "/pipelines/signal",
        json={"correlation_id": "CORR-2", "event": "design.kickoff", "mode": "skip"},
    )
    assert resp.status_code == 403
    # denied → never delivered
    assert delivered == []
    # ...but the denial IS on the append-only audit ("who tried to skip, and why")
    events = _ingress_events(gov)
    assert len(events) == 1
    assert events[0].payload["mode"] == "skip"
    assert events[0].payload["allowed"] is False
    assert events[0].policy_decision == "deny"
    assert events[0].payload["correlation_id"] == "CORR-2"
    assert events[0].payload["event"] == "design.kickoff"


# ---- (d) mode=skip WITH the scope → delivered AND audited --------------------


def test_signal_skip_with_scope_is_delivered_and_audited(client: TestClient) -> None:
    # governance grants pipeline:skip to the caller → authorized
    gov = MockGovernanceProvider(allowed_scopes=frozenset({"pipeline:skip"}))
    _install_governance(client, gov)
    delivered: list[tuple[str, str]] = []

    async def fake_signal(correlation_id: str, event: str) -> None:
        delivered.append((correlation_id, event))

    client.app.state.pipeline_signal = fake_signal  # type: ignore[attr-defined]
    resp = client.post(
        "/pipelines/signal",
        json={
            "correlation_id": "CORR-3",
            "event": "design.kickoff",
            "mode": "skip",
            "source_event_id": "evt-99",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["delivered"] is True
    assert delivered == [("CORR-3", "design.kickoff")]
    events = _ingress_events(gov)
    assert len(events) == 1
    assert events[0].payload["allowed"] is True
    assert events[0].policy_decision == "allow"
    # source_event_id is passed through for the orchestrator's dedup (runtime keeps none)
    assert events[0].payload["source_event_id"] == "evt-99"


# ---- (e) unset sink → 503 ----------------------------------------------------


def test_signal_sink_unset_is_503(client: TestClient) -> None:
    gov = MockGovernanceProvider()
    _install_governance(client, gov)
    # no app.state.pipeline_signal set
    resp = client.post(
        "/pipelines/signal",
        json={"correlation_id": "CORR-4", "event": "build.ready-in-qa", "mode": "emit"},
    )
    assert resp.status_code == 503
    assert "signal seam not configured" in resp.json()["detail"]
