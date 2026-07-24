"""Tests for the generic orchestration drive seam (design/details/orchestration-provider-seam.md).

Covers the runtime-side, domain-neutral half of the seam only:

- the drive-contract types (``StageOutcome`` / ``StageStatus`` / ``RunStage``);
- ``POST /pipelines/run-stage`` — driven by an injected fake ``RunStage``, mirroring how the
  orchestrator demos inject ``run_stage``;
- ``GET /pipelines/gate-status/{correlation_id}/{gate}`` — read off the existing review queue.

The ``OrchestrationProvider`` / ``SagaView`` / reference controller / Temporal adapter live in the
example, not the runtime, and are not exercised here.
"""

from __future__ import annotations

import dataclasses
import shutil
import typing
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.orchestration import RunStage, StageOutcome, StageStatus
from swarmkit_runtime.review import FileReviewQueue, ReviewItem

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


# ---- drive-contract types ----------------------------------------------------


def test_stage_outcome_defaults() -> None:
    outcome = StageOutcome(status="completed")
    assert outcome.status == "completed"
    assert outcome.artifact == ""
    assert outcome.detail == ""


def test_stage_outcome_is_frozen() -> None:
    outcome = StageOutcome(status="parked", artifact="<draft>", detail="on gate")
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.status = "completed"  # type: ignore[misc]


def test_stage_status_members() -> None:
    assert set(typing.get_args(StageStatus)) == {
        "completed",
        "parked",
        "rejected",
        "denied",
        "failed",
    }


@pytest.mark.asyncio
async def test_run_stage_type_is_correlation_first() -> None:
    """A scripted ``RunStage`` takes an opaque correlation id + stage spec, returns an outcome."""
    seen: list[str] = []

    async def run_stage(correlation_id: str, stage: dict[str, Any]) -> StageOutcome:
        seen.append(correlation_id)
        return StageOutcome(status="completed", artifact=f"<{stage['id']}>")

    seam: RunStage = run_stage
    outcome = await seam("corr-1", {"id": "intake"})
    assert seen == ["corr-1"]
    assert outcome == StageOutcome(status="completed", artifact="<intake>")


# ---- serve endpoints ---------------------------------------------------------


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    """An isolated copy of the example workspace (so review writes don't touch the repo)."""
    dest = tmp_path / "workspace"
    shutil.copytree(EXAMPLE_WS, dest)
    return dest


@pytest.fixture()
def client(ws: Path) -> Iterator[TestClient]:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws)
    with TestClient(app) as c:
        yield c


def test_run_stage_seam_unconfigured_is_503(client: TestClient) -> None:
    resp = client.post(
        "/pipelines/run-stage", json={"correlation_id": "corr-1", "stage": {"id": "intake"}}
    )
    assert resp.status_code == 503
    assert "run-stage seam not configured" in resp.json()["detail"]


def test_run_stage_drives_injected_seam(client: TestClient) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_run_stage(correlation_id: str, stage: dict[str, Any]) -> StageOutcome:
        calls.append((correlation_id, stage))
        return StageOutcome(status="parked", artifact="<design>", detail="on funnel gate")

    client.app.state.pipeline_run_stage = fake_run_stage  # type: ignore[attr-defined]
    resp = client.post(
        "/pipelines/run-stage",
        json={"correlation_id": "REQ-7", "stage": {"id": "design", "gate": True}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "parked", "artifact": "<design>", "detail": "on funnel gate"}
    assert calls == [("REQ-7", {"id": "design", "gate": True})]


def _seed_gate_item(queue: FileReviewQueue, *, gate_id: str, role: str, status: str) -> None:
    queue.submit(
        ReviewItem(
            id=f"mpa-{gate_id}-0-{role}",
            topology_id="REQ-7",
            agent_id="designer",
            skill_id="multi-party-approval",
            output={"gate_id": gate_id, "role": role, "scope": "app:oms:approve"},
            verdict={},
            reason=f"role {role!r} must approve",
            timestamp=datetime.now(tz=UTC),
            status=status,  # type: ignore[arg-type]
        )
    )


def test_gate_status_pending_when_unopened(client: TestClient) -> None:
    resp = client.get("/pipelines/gate-status/REQ-7/designer")
    assert resp.status_code == 200
    assert resp.json() == {"correlation_id": "REQ-7", "gate": "designer", "status": "pending"}


def test_gate_status_approved_when_all_approved(client: TestClient, ws: Path) -> None:
    queue = FileReviewQueue(ws)
    _seed_gate_item(queue, gate_id="REQ-7:designer", role="oms-lead", status="approved")
    _seed_gate_item(queue, gate_id="REQ-7:designer", role="web-lead", status="approved")
    resp = client.get("/pipelines/gate-status/REQ-7/designer")
    assert resp.json()["status"] == "approved"


def test_gate_status_rejected_when_any_rejected(client: TestClient, ws: Path) -> None:
    queue = FileReviewQueue(ws)
    _seed_gate_item(queue, gate_id="REQ-7:designer", role="oms-lead", status="approved")
    _seed_gate_item(queue, gate_id="REQ-7:designer", role="web-lead", status="rejected")
    resp = client.get("/pipelines/gate-status/REQ-7/designer")
    assert resp.json()["status"] == "rejected"


def test_gate_status_pending_when_partly_resolved(client: TestClient, ws: Path) -> None:
    queue = FileReviewQueue(ws)
    _seed_gate_item(queue, gate_id="REQ-7:designer", role="oms-lead", status="approved")
    _seed_gate_item(queue, gate_id="REQ-7:designer", role="web-lead", status="pending")
    resp = client.get("/pipelines/gate-status/REQ-7/designer")
    assert resp.json()["status"] == "pending"


def test_gate_status_accepts_fully_qualified_gate_id(client: TestClient, ws: Path) -> None:
    """The gate arg may be the bare gate name the StageRunner composes, matched on ``gate_id``."""
    queue = FileReviewQueue(ws)
    _seed_gate_item(queue, gate_id="REQ-7:designer", role="oms-lead", status="approved")
    # querying with the fully-qualified id also resolves (correlation_id + the composed gate)
    resp = client.get("/pipelines/gate-status/REQ-7/REQ-7:designer")
    assert resp.json()["status"] == "approved"
