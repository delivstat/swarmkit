"""Tests for the webhook → pipeline ingress receiver (37c).

Covers design/details/pipeline-triggering.md §"Structured webhook": a signed ``Trigger`` whose
target is a ``pipeline_target`` turns a webhook into a scoped ``emit`` on the ingress front door,
while a topology-id webhook keeps its existing job-start behaviour.

- the dotted-path ``correlation_id`` extractor resolves ``$.a.b.c`` and rejects non-scalars;
- a signed webhook with a ``pipeline_target`` routes to the ingress (a fake sink) with the
  extracted ``correlation_id`` and the trigger's *declared* ``emit`` event, and is audited;
- a webhook whose body asks for a different event (or a non-``emit`` mode) is refused (403);
- a bad HMAC signature is rejected (401);
- back-compat: a topology-id webhook still starts a job.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.triggers import extract_correlation_id, find_pipeline_webhook_trigger

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"

_CI_SECRET = "ci-shared-secret"
_TRIGGER_YAML = """\
apiVersion: swarmkit/v1
kind: Trigger
metadata:
  id: ci-build-ready
  name: CI build-ready webhook
type: webhook
targets:
  - pipeline: oms-pipeline
    emit: build.ready-in-qa
    correlation_id: $.body.correlation_id
config:
  auth:
    method: hmac
    credentials_ref: CI_WEBHOOK_SECRET
"""


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("CI_WEBHOOK_SECRET", _CI_SECRET)


# ---- (a) the dotted-path extractor (unit) ------------------------------------


def test_extract_correlation_id_dotted_path() -> None:
    payload = {"body": {"correlation_id": "OMS-101"}, "top": "T-1"}
    assert extract_correlation_id(payload, "$.body.correlation_id") == "OMS-101"
    assert extract_correlation_id(payload, "$.top") == "T-1"
    # a bare (no-$) path is also accepted
    assert extract_correlation_id(payload, "body.correlation_id") == "OMS-101"


def test_extract_correlation_id_coerces_scalars_and_rejects_containers() -> None:
    payload = {"n": 42, "obj": {"a": 1}, "arr": [1, 2], "flag": True, "nil": None}
    # a numeric id is still an opaque handle → coerced to str
    assert extract_correlation_id(payload, "$.n") == "42"
    # missing / non-scalar / bool / null → None (a "could not extract", never a silent drop)
    assert extract_correlation_id(payload, "$.missing") is None
    assert extract_correlation_id(payload, "$.obj") is None
    assert extract_correlation_id(payload, "$.arr") is None
    assert extract_correlation_id(payload, "$.flag") is None
    assert extract_correlation_id(payload, "$.nil") is None
    assert extract_correlation_id(payload, "") is None


def test_find_pipeline_webhook_trigger_matches_by_id_and_type() -> None:
    configs: list[dict[str, Any]] = [
        {"id": "ci-build-ready", "type": "webhook", "pipeline_targets": [{"emit": "x"}]},
        {"id": "topo-hook", "type": "webhook", "targets": ["hello"], "pipeline_targets": []},
        {"id": "disabled", "type": "webhook", "enabled": False, "pipeline_targets": [{"e": 1}]},
    ]
    assert find_pipeline_webhook_trigger(configs, "ci-build-ready") is not None
    # a topology-only webhook is not a pipeline trigger (back-compat routing)
    assert find_pipeline_webhook_trigger(configs, "topo-hook") is None
    # a disabled trigger does not fire
    assert find_pipeline_webhook_trigger(configs, "disabled") is None
    assert find_pipeline_webhook_trigger(configs, "nope") is None


# ---- serve fixtures ----------------------------------------------------------


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    dest = tmp_path / "workspace"
    shutil.copytree(EXAMPLE_WS, dest)
    (dest / "triggers").mkdir(exist_ok=True)
    (dest / "triggers" / "ci-build-ready.yaml").write_text(_TRIGGER_YAML)
    return dest


@pytest.fixture()
def client(ws: Path) -> Iterator[TestClient]:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws)
    with TestClient(app) as c:
        yield c


def _install_governance(client: TestClient, gov: MockGovernanceProvider) -> None:
    client.app.state.runtime._governance = gov  # type: ignore[attr-defined]


def _ingress_events(gov: MockGovernanceProvider) -> list[AuditEvent]:
    return [e for e in gov.events if e.event_type == "pipeline.ingress"]


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(_CI_SECRET.encode(), body, hashlib.sha256).hexdigest()


# ---- (b) a signed pipeline webhook routes to the ingress sink ----------------


def test_signed_pipeline_webhook_emits_declared_event(client: TestClient) -> None:
    gov = MockGovernanceProvider()  # emit needs no scope
    _install_governance(client, gov)
    delivered: list[tuple[str, str]] = []

    async def fake_signal(correlation_id: str, event: str) -> None:
        delivered.append((correlation_id, event))

    client.app.state.pipeline_signal = fake_signal  # type: ignore[attr-defined]

    payload = {"body": {"correlation_id": "OMS-101"}, "source_event_id": "ci-42"}
    body = json.dumps(payload).encode()
    resp = client.post(
        "/hooks/ci-build-ready",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["delivered"] is True
    assert data["trigger"] == "ci-build-ready"
    assert data["source"] == "webhook:ci-build-ready"
    assert data["signals"] == [
        {"pipeline": "oms-pipeline", "correlation_id": "OMS-101", "event": "build.ready-in-qa"}
    ]
    # the extracted correlation_id + the trigger's declared emit reached the sink
    assert delivered == [("OMS-101", "build.ready-in-qa")]
    # every ingress attempt is audited, stamped with the webhook source + passed-through dedup id
    events = _ingress_events(gov)
    assert len(events) == 1
    assert events[0].payload["mode"] == "emit"
    assert events[0].payload["allowed"] is True
    assert events[0].payload["source"] == "webhook:ci-build-ready"
    assert events[0].payload["event"] == "build.ready-in-qa"
    assert events[0].payload["source_event_id"] == "ci-42"


# ---- (c) a webhook may emit ONLY its declared event --------------------------


def test_pipeline_webhook_refuses_undeclared_event(client: TestClient) -> None:
    gov = MockGovernanceProvider(allowed_scopes=frozenset({"pipeline:skip"}))
    _install_governance(client, gov)
    delivered: list[tuple[str, str]] = []

    async def fake_signal(correlation_id: str, event: str) -> None:
        delivered.append((correlation_id, event))

    client.app.state.pipeline_signal = fake_signal  # type: ignore[attr-defined]

    # the body tries to smuggle a different event — refused, even though the *signature* is valid
    payload = {"body": {"correlation_id": "OMS-101"}, "event": "design.approved"}
    body = json.dumps(payload).encode()
    resp = client.post(
        "/hooks/ci-build-ready",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 403
    assert delivered == []


def test_pipeline_webhook_refuses_operator_mode(client: TestClient) -> None:
    gov = MockGovernanceProvider()
    _install_governance(client, gov)
    client.app.state.pipeline_signal = _noop_signal  # type: ignore[attr-defined]

    payload = {"body": {"correlation_id": "OMS-101"}, "mode": "skip"}
    body = json.dumps(payload).encode()
    resp = client.post(
        "/hooks/ci-build-ready",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 403


# ---- (d) a bad signature is rejected -----------------------------------------


def test_pipeline_webhook_bad_signature_is_401(client: TestClient) -> None:
    gov = MockGovernanceProvider()
    _install_governance(client, gov)
    client.app.state.pipeline_signal = _noop_signal  # type: ignore[attr-defined]

    payload = {"body": {"correlation_id": "OMS-101"}}
    body = json.dumps(payload).encode()
    resp = client.post(
        "/hooks/ci-build-ready",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=deadbeef", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    # never audited nor delivered — rejected before the guardrail
    assert _ingress_events(gov) == []


# ---- (e) back-compat: a topology-id webhook still starts a job ---------------


def test_topology_webhook_still_starts_a_job(client: TestClient) -> None:
    resp = client.post("/hooks/hello", json={"input": "hi"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "running"
    assert data["job_id"]


async def _noop_signal(correlation_id: str, event: str) -> None:
    return None
