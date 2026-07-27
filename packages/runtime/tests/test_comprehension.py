"""Comprehension-debt telemetry — slice 3 of gate-coverage-and-comprehension-debt.

Unit tests drive the pure signal logic with synthetic audit events; the CLI + endpoint
tests run over the SDLC example workspace (robust to whether its .swarmkit audit store is
present — an absent store is a valid "nothing to assess", not a failure).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from swarmkit_runtime.cli import app
from swarmkit_runtime.comprehension import compute_comprehension
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.server import create_app
from typer.testing import CliRunner

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SDLC_WS = _REPO_ROOT / "examples" / "sdlc-pipeline" / "workspace"
_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _ev(
    offset_s: float, event_type: str, payload: dict[str, object], run: str = "r1"
) -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        agent_id="a",
        timestamp=_BASE + timedelta(seconds=offset_s),
        payload=payload,
        run_id=run,
    )


def test_fast_approve_detected_and_slow_ignored() -> None:
    events = [
        # g1: opened → approved 3s later → FAST
        _ev(0, "approval.gate_opened", {"gate_id": "g1"}),
        _ev(
            3,
            "approval.gate_resolved",
            {"gate_id": "g1", "status": "approved", "approvers": ["alice"]},
        ),
        # g2 (run r2): approved 120s later → slow, not flagged
        _ev(0, "approval.gate_opened", {"gate_id": "g2"}, run="r2"),
        _ev(
            120,
            "approval.gate_resolved",
            {"gate_id": "g2", "status": "approved", "approvers": ["bob", "carol"]},
            run="r2",
        ),
        # g3: approved but no paired open → counted, not timable
        _ev(0, "approval.gate_resolved", {"gate_id": "g3", "status": "approved"}, run="r3"),
        # g4: rejected → not an approval at all
        _ev(1, "approval.gate_resolved", {"gate_id": "g4", "status": "rejected"}, run="r4"),
    ]
    r = compute_comprehension(events, fast_approve_threshold_seconds=20.0)
    assert r.approvals_seen == 3  # g1, g2, g3 (g4 rejected excluded)
    assert len(r.fast_approvals) == 1
    fa = r.fast_approvals[0]
    assert fa.gate_id == "g1"
    assert fa.latency_seconds == 3.0
    assert fa.distinct_approvers == 1
    assert "rubber-stamp" in r.verdict()
    assert r.deferred  # deferred signals are always disclosed


def test_threshold_is_strict_and_pairs_by_run_and_gate() -> None:
    events = [
        _ev(0, "approval.gate_opened", {"gate_id": "g"}),
        _ev(
            20, "approval.gate_resolved", {"gate_id": "g", "status": "approved", "approvers": ["x"]}
        ),
    ]
    # latency == threshold is NOT below it → not flagged
    assert compute_comprehension(events, fast_approve_threshold_seconds=20.0).fast_approvals == ()
    assert (
        len(compute_comprehension(events, fast_approve_threshold_seconds=21.0).fast_approvals) == 1
    )


def test_empty_window() -> None:
    r = compute_comprehension([])
    assert r.approvals_seen == 0
    assert "nothing to assess" in r.verdict()


def test_comprehension_cli() -> None:
    result = CliRunner().invoke(app, ["comprehension", str(_SDLC_WS)])
    assert result.exit_code == 0, result.output


def test_comprehension_endpoint() -> None:
    with TestClient(create_app(_SDLC_WS)) as c:
        r = c.get("/comprehension")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "verdict" in body
        assert "approvals_seen" in body
        assert isinstance(body["fast_approvals"], list)
        assert body["deferred"]  # deferred signals disclosed
