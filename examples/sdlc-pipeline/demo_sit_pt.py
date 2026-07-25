"""Demo: cross-app SIT + PT against mock rigs, then the pre-release security review (slice 8).

The determination detail behind the defect loop (``demo_defect_loop.py`` is the sequencing
centerpiece). Deterministic — no model calls, no harness, no live server, no network. All seams
are faked in ``sit_pt.py``:

  ① SIT — the ``sit-qa`` engineer runs the cross-app e2e business flows (oms/web/mobile) against the
     MOCK QA rig. The clean run passes every flow; a scripted failing flow is the SIT defect the
     controller-driven loop re-tests after the fix.
  ② PT — the ``pt-engineer`` runs a perf test of the exposed services against the MOCK perf rig and
     the ``pt-analysis`` decision skill judges the samples against the agreed thresholds: pass, or a
     regression (a breached SLO) that is filed as a defect.
  ③ Security review — the pre-release ``security-review-approval`` funnel: an investigative
     ``security-consultant`` HARNESS review (compliance / SAST / DAST) whose HIGH-severity finding
     routes back before the ``infosec-lead`` signs off (the human approval is the only exit).

Run it:

    uv run python examples/sdlc-pipeline/demo_sit_pt.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from swarmkit_runtime.resolver import resolve_workspace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sit_pt

WS = Path(__file__).resolve().parent / "workspace"


def demo_sit() -> None:
    print("① SIT — cross-app e2e business flows on the mock QA rig (oms/web/mobile)")
    for flow in sit_pt.run_sit_rig():
        apps = "+".join(flow.apps)
        print(f"   {'PASS' if flow.passed else 'FAIL'}  {flow.flow:<38} [{apps}]")
    # The failing-flow variant is what raises the SIT defect the controller loops on.
    failed = [f for f in sit_pt.run_sit_rig(failing_flow="checkout") if not f.passed]
    print(f"   (a scripted failing flow raises a defect: {failed[0].flow} — {failed[0].detail})\n")


def demo_pt() -> None:
    print("② PT — perf test of the exposed services on the mock rig → pt-analysis vs thresholds")
    clean = sit_pt.run_pt_rig()
    for s in clean:
        print(f"   {s.service:<18} p95={s.p95_ms:>5.0f}ms  err={s.error_rate:.3f}")
    verdict = sit_pt.pt_analysis(clean)
    print(f"   pt-analysis → {verdict.verdict.upper()}: {verdict.reasoning}")
    regressed = sit_pt.pt_analysis(sit_pt.run_pt_rig(regression=True))
    print(
        f"   (a latency regression → pt-analysis {regressed.verdict.upper()}: "
        f"{'; '.join(regressed.breaches)})\n"
    )


async def demo_security(ws: object) -> None:
    print("③ Security review — the security-consultant harness review + infosec sign-off")
    print("  ▸ clean review:")
    clean = await sit_pt.run_security_review_gate(
        ws, review_script=["clean"], correlation_id="SEC-101", verbose=True
    )
    print(
        f"   GATE OUTCOME: {clean.outcome.upper()}  retries={clean.retries}  "
        f"approved by {', '.join(sorted(clean.approvers))}\n"
    )
    print("  ▸ HIGH-severity finding routes back, then the revision clears:")
    routed = await sit_pt.run_security_review_gate(
        ws, review_script=["high", "clean"], correlation_id="SEC-102", verbose=True
    )
    print(
        f"   GATE OUTCOME: {routed.outcome.upper()}  retries={routed.retries}  "
        f"approved by {', '.join(sorted(routed.approvers))}\n"
    )


async def main() -> None:
    ws = resolve_workspace(WS)
    demo_sit()
    demo_pt()
    await demo_security(ws)
    print("✓ SIT/PT + security-review demo complete")


if __name__ == "__main__":
    asyncio.run(main())
