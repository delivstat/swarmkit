"""Demo: cited_change wired as a funnel validate layer, with the diff threaded to the gate
(funnel-deterministic-validate.md, step 2).

The gated node's artifact is a change-rationale (`summary` + `citations`); its produced diff is
threaded to the gate via ``diff_source`` (a harness executor surfaces it; here it's fixed). An
uncited change — a citation the diff did not change — is a validate failure that drives the funnel's
bounded retry ('cite it'), then escalates to the human approve layer (never drops). Deterministic;
no keys, no server.
"""

from __future__ import annotations

import asyncio
from typing import Any

from swarmkit_runtime.langgraph_compiler._gate_funnel import (
    ApproveOutcome,
    build_deterministic_validator,
    compile_funnel_gate,
)

# The gated node's produced diff (a harness executor would collect this from its worktree).
_DIFF = "--- a/reserve.py\n+++ b/reserve.py\n@@ -1,1 +1,3 @@\n a\n+reserve stock\n+commit\n"

# A change-rationale that cites a line the diff changed → resolves.
_CITED = (
    "summary: add stock-reservation to reserve()\n"
    "citations:\n"
    "  - claim: reserve() now reserves before committing\n"
    "    path: reserve.py\n"
    "    lines: [2]\n"
)
# A change-rationale citing a line the diff never touched → uncited.
_UNCITED = (
    "summary: add stock-reservation to reserve()\n"
    "citations:\n"
    "  - claim: reserve() now reserves before committing\n"
    "    path: reserve.py\n"
    "    lines: [99]\n"
)

_SPEC: dict[str, Any] = {
    "validate": {"cited_change": True},
    "approve": {"rules": [{"scope": "release:approve", "roles": ["lead"], "quorum": "all"}]},
}


async def _human_approve(state: dict[str, Any]) -> ApproveOutcome:
    return ApproveOutcome(approved=True, detail="human signed off")


async def _run(label: str, rationale: str) -> None:
    async def drafter(state: dict[str, Any]) -> str:
        return rationale  # the node produces the change-rationale; the diff is threaded separately

    compiled = compile_funnel_gate(
        _SPEC,
        drafter=drafter,
        approver=_human_approve,
        validator=build_deterministic_validator(_SPEC),
        diff_source=lambda: _DIFF,  # the harness-produced diff reaches validate via ValidateContext
    )
    result = await compiled.ainvoke({"artifact": rationale, "retries": 0})
    prov = result["provenance"]
    print(f"\n{label}")
    outcome, escalated, retries = result["outcome"], prov["escalated"], result["retries"]
    print(f"  outcome: {outcome}   escalated: {escalated}   retries: {retries}")


async def main() -> None:
    print(
        "cited_change as a funnel validate layer — every citation must resolve to a changed line:"
    )
    await _run("cited change   (cites reserve.py:2, which the diff changed)", _CITED)
    await _run("uncited change (cites reserve.py:99, which the diff never touched)", _UNCITED)
    print("\n✓ uncited change retried, then escalated to the human approve — never auto-advanced.")


if __name__ == "__main__":
    asyncio.run(main())
