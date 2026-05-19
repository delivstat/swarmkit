"""Inline HITL — pause execution and ask the human in the terminal.

See ``design/details/decision-skills.md`` §Layer 1: inline HITL.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Literal


class HITLDeferredError(Exception):
    """Raised when human defers review (Ctrl+C). Run can be resumed later."""

    def __init__(self, agent_id: str, skill_id: str, reason: str) -> None:
        self.agent_id = agent_id
        self.skill_id = skill_id
        self.reason = reason
        super().__init__(
            f"Review deferred for {agent_id}/{skill_id}: {reason}. "
            f"Use `swarmkit review approve <id>` then `swarmkit run --resume` to continue."
        )


def prompt_human_review(
    *,
    agent_id: str,
    skill_id: str,
    output: dict[str, Any],
    verdict: dict[str, Any] | None,
    reason: str,
    interactive: bool = True,
) -> Literal["approved", "rejected"]:
    """Pause and ask the human to approve or reject.

    Returns ``"approved"`` or ``"rejected"``.

    If the human presses Ctrl+C, raises ``HITLDeferredError`` so the
    runtime can checkpoint state and exit cleanly. The run can be
    resumed after the review is approved.

    If not interactive (stdin is not a TTY), defaults to ``"rejected"``
    — unattended execution should not silently approve.
    """
    if not interactive or not sys.stdin.isatty():
        print(
            f"\n⏸ Review required for {agent_id}/{skill_id}: {reason}",
            file=sys.stderr,
        )
        print("  (non-interactive — auto-rejecting)", file=sys.stderr)
        return "rejected"

    print(f"\n⏸ Review required: {skill_id} on agent '{agent_id}'")
    print(f"  Reason: {reason}")
    print(f"  Output: {json.dumps(output, indent=2)[:500]}")
    if verdict:
        print(f"  Verdict: {json.dumps(verdict, indent=2)[:300]}")
    print()

    while True:
        try:
            choice = input("  [a]pprove  [r]eject  [s]how full output > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  ⏸ Deferred — run state checkpointed.")
            print("  Approve later with: swarmkit review approve <id>")
            print("  Resume with: swarmkit run --resume")
            raise HITLDeferredError(agent_id, skill_id, reason) from None

        if choice in ("a", "approve"):
            print("  ✓ Approved. Continuing execution.")
            return "approved"
        if choice in ("r", "reject"):
            print("  ✗ Rejected.")
            return "rejected"
        if choice in ("s", "show"):
            print(f"\n  Full output:\n{json.dumps(output, indent=2)}")
            if verdict:
                print(f"\n  Full verdict:\n{json.dumps(verdict, indent=2)}")
            print()
