"""The flag-cleanup example loads, validates, and its two-phase demo runs deterministically.

Runs in CI (examples/flag-cleanup/tests is on testpaths) on the mock provider — no keys, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime

EXAMPLE = Path(__file__).resolve().parents[1]
WS = EXAMPLE / "workspace"


def test_workspace_resolves_and_verifies() -> None:
    rt = WorkspaceRuntime.from_workspace_path(WS)
    assert set(rt.workspace.topologies) == {"flag-triage", "flag-cleanup"}
    # Every declared funnel layer is wired (no inert config), and both roots are gated.
    assert rt.reachability().ok, rt.reachability().blocking
    assert rt.verification().unverified_roots == (), rt.verification().unverified_roots


@pytest.mark.asyncio
async def test_the_demo_both_scenarios_end_approved() -> None:
    sys.path.insert(0, str(EXAMPLE))
    import demo  # noqa: PLC0415

    clean = await demo._run_gate(verdicts=[True], clean_first=True, correlation_id="T-CLEAN")
    assert clean == "approved"
    # The flawed first pass is caught and the revision passes — one route-back, then approved.
    routed = await demo._run_gate(
        verdicts=[False, True], clean_first=False, correlation_id="T-BACK"
    )
    assert routed == "approved"


@pytest.mark.asyncio
async def test_the_harness_diff_removes_every_flag_reference() -> None:
    sys.path.insert(0, str(EXAMPLE))
    import demo  # noqa: PLC0415

    diff, telem = await demo._run_harness(revised=True)
    assert "checkout_v2_enabled" not in _added_lines(diff), "clean diff leaves no flag ref"
    assert telem["status"] == "success"
    # The flawed pass DOES still reference it — that is what the review catches.
    flawed, _ = await demo._run_harness(revised=False)
    assert "checkout_v2_enabled" in flawed  # the leftover the route-back exists to find


def _added_lines(diff: str) -> str:
    return "\n".join(line for line in diff.splitlines() if line.startswith("+"))
