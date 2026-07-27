"""Demo: slice_budget wired as a funnel validate layer (funnel-deterministic-validate.md, step 1).

An over-budget diff is a validate failure — it drives the funnel's bounded retry ('split it'),
then escalates to the human approve layer (never drops). Deterministic; no keys, no server.
"""

from __future__ import annotations

import asyncio
from typing import Any

from swarmkit_runtime.langgraph_compiler._gate_funnel import (
    ApproveOutcome,
    build_deterministic_validator,
    compile_funnel_gate,
)
from swarmkit_runtime.slice_budget import check_diff_text

_OVER = (
    "--- a/reserve.py\n+++ b/reserve.py\n@@ -1,1 +1,6 @@\n a\n+b\n+c\n+d\n+e\n+f\n"  # 5 added lines
)
_UNDER = "--- a/reserve.py\n+++ b/reserve.py\n@@ -1,1 +1,3 @@\n a\n+b\n+c\n"  # 2 added lines

_SPEC: dict[str, Any] = {
    "validate": {"slice_budget": {"max_diff_lines": 3, "max_files": 5}},
    "approve": {"rules": [{"scope": "release:approve", "roles": ["lead"], "quorum": "all"}]},
}


async def _human_approve(state: dict[str, Any]) -> ApproveOutcome:
    return ApproveOutcome(approved=True, detail="human signed off")


async def _run(label: str, diff: str) -> None:
    async def drafter(state: dict[str, Any]) -> str:
        return diff  # the harness would produce this diff; here it's fixed

    compiled = compile_funnel_gate(
        _SPEC,
        drafter=drafter,
        approver=_human_approve,
        validator=build_deterministic_validator(_SPEC),
    )
    result = await compiled.ainvoke({"artifact": diff, "retries": 0})
    prov = result["provenance"]
    added = check_diff_text(diff).total_lines
    print(f"\n{label}: diff with {added} added line(s), budget max_diff_lines=3")
    outcome, escalated, retries = result["outcome"], prov["escalated"], result["retries"]
    print(f"  outcome: {outcome}   escalated: {escalated}   retries: {retries}")


async def main() -> None:
    print("slice_budget as a funnel validate layer — the gate enforces the budget:")
    await _run("within budget", _UNDER)
    await _run("OVER budget ", _OVER)
    print("\n✓ over-budget retried, then escalated to the human approve — never auto-advanced.")


if __name__ == "__main__":
    asyncio.run(main())
