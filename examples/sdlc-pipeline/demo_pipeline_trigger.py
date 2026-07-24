"""Demo: the webhook → pipeline ingress path (37c, design/details/pipeline-triggering.md).

Turns a *real* signed webhook into the structured `(correlation_id, event)` an orchestrator
sequences on — the "structured webhook" front door — with the governance guardrail that keeps a
webhook scoped to emitting, never advancing or skipping a stage. Self-contained and deterministic:
no live server (the app runs in-process under `TestClient`), no model calls, no API budget. The
reference `PipelineController` owns the durable saga; SwarmKit only authorises, audits, and hands
the event to the injected `pipeline_signal` sink.

Shows:
  (a) a **signed CI webhook** carrying `{correlation_id: OMS-101, ...}` for the `ci-build-ready`
      Trigger (a `pipeline_target` that emits `build.ready-in-qa`) → HMAC validated → the reference
      saga advances build → sit → done. Prints the correlated timeline.
  (c) an **unauthorised skip** (`mode=skip`, no `pipeline:skip` scope) → denied (403) *and* audited;
      prints the append-only audit line proving it was refused, not silently dropped.

The chat→interpreter path (b) is deferred (a later PR).

Run it:

    uv run python examples/sdlc-pipeline/demo_pipeline_trigger.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path

from controller import (
    InboundEvent,
    PipelineController,
    StageGraph,
    StageRunOutcome,
    StageRunRequest,
)
from fastapi.testclient import TestClient
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.server import create_app

WS = Path(__file__).resolve().parent / "workspace"
CI_SECRET = "ci-shared-secret"  # the CI_WEBHOOK_SECRET the ci-build-ready Trigger references
GATED_TOPOLOGIES = {"oms-design"}  # the design stage parks on its funnel gate


def _load_graph() -> StageGraph:
    ws = resolve_workspace(WS)
    return StageGraph.from_spec(ws.stage_graphs["oms-pipeline"].spec)


def make_seam(kicked: list[str]) -> object:
    async def run_stage(request: StageRunRequest) -> StageRunOutcome:
        kicked.append(f"{request.correlation_id}:{request.topology}")
        if request.topology in GATED_TOPOLOGIES:
            return StageRunOutcome(status="parked")
        return StageRunOutcome(status="completed")

    return run_stage


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(CI_SECRET.encode(), body, hashlib.sha256).hexdigest()


def print_timeline(controller: PipelineController, correlation_id: str) -> None:
    saga = controller.saga(correlation_id)
    assert saga is not None
    print(f"\n── correlated saga timeline: {correlation_id}  [status={saga.status.upper()}] ──")
    for entry in saga.timeline:
        stage = f"[{entry.stage_id}]" if entry.stage_id else "[-]"
        print(f"  {entry.seq:>3} {stage:<10} {entry.kind:<22} {entry.detail}")


async def _predrive(controller: PipelineController) -> None:
    """Drive OMS-101 up to the external CI wait: intake → design (gate approved) → build → wait.

    build's success (`build.ready-in-qa`) is an EXTERNAL CI event the controller never fabricates —
    it waits for the webhook. That webhook is exactly what path (a) delivers.
    """
    await controller.handle_event(InboundEvent("OMS-101", "requirement.created", "jira-1"))
    await controller.resolve_gate("OMS-101", approved=True)


async def main() -> None:
    os.environ["CI_WEBHOOK_SECRET"] = CI_SECRET
    kicked: list[str] = []
    controller = PipelineController(
        _load_graph(),
        make_seam(kicked),  # type: ignore[arg-type]
        external_events=("build.ready-in-qa",),
    )
    await _predrive(controller)
    s = controller.saga("OMS-101")
    assert s is not None
    print(
        f"SETUP — OMS-101 is at build, status={s.status}, passed={s.passed_stages}, "
        f"awaiting the external 'build.ready-in-qa' CI webhook"
    )

    # The ingress signal sink: an authorised pipeline event → the reference controller. This is the
    # `app.state.pipeline_signal` seam — the runtime owns no orchestrator, exactly like run-stage.
    async def pipeline_signal(correlation_id: str, event: str) -> None:
        await controller.handle_event(
            InboundEvent(correlation_id, event, source_event_id=f"ingress:{event}")
        )

    app = create_app(WS)
    with TestClient(app) as client:
        # Deterministic, assertable governance: emit needs no scope; skip is denied (no scope).
        gov = MockGovernanceProvider(allowed_scopes=frozenset())
        client.app.state.runtime._governance = gov  # type: ignore[attr-defined]
        client.app.state.pipeline_signal = pipeline_signal  # type: ignore[attr-defined]

        print("\n(a) SIGNED CI WEBHOOK — POST /hooks/ci-build-ready  {correlation_id: OMS-101}")
        payload = {"correlation_id": "OMS-101", "source_event_id": "ci-build-42", "status": "green"}
        body = json.dumps(payload).encode()
        resp = client.post(
            "/hooks/ci-build-ready",
            content=body,
            headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
        )
        print(f"    → HTTP {resp.status_code}  {json.dumps(resp.json())}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["signals"] == [
            {"pipeline": "oms-pipeline", "correlation_id": "OMS-101", "event": "build.ready-in-qa"}
        ]

        s = controller.saga("OMS-101")
        assert s is not None and s.status == "done", f"expected done, got {s.status}"
        print(f"    → saga advanced: status={s.status.upper()}  passed={s.passed_stages}")
        print_timeline(controller, "OMS-101")

        print("\n(c) UNAUTHORISED SKIP — POST /pipelines/signal  mode=skip  (no scope)")
        resp = client.post(
            "/pipelines/signal",
            json={"correlation_id": "OMS-777", "event": "design.kickoff", "mode": "skip"},
        )
        print(f"    → HTTP {resp.status_code}  (denied — a skip is a reserved operator act)")
        assert resp.status_code == 403, resp.text

        denials = [
            e
            for e in gov.events
            if e.event_type == "pipeline.ingress" and e.payload.get("allowed") is False
        ]
        assert denials, "the denied skip must be on the append-only audit"
        _print_audit_denial(denials[-1])

    print("\n✓ pipeline-trigger demo complete")


def _print_audit_denial(event: AuditEvent) -> None:
    p = event.payload
    print("    → AUDIT (append-only, proving it was refused not dropped):")
    print(
        f"        pipeline.ingress  decision={event.policy_decision}  "
        f"source={p.get('source')}  correlation_id={p.get('correlation_id')}  "
        f"event={p.get('event')}  mode={p.get('mode')}  allowed={p.get('allowed')}"
    )
    print(f"        reason: {event.policy_reason}")


if __name__ == "__main__":
    asyncio.run(main())
