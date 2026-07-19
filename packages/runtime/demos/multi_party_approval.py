"""Demo: multi-party approval gate (design/details/multi-party-approval.md).

A gate fans out into one task per required role; it advances only when every rule's quorum is met
across distinct human identities, plus any four-eyes floor. This runs the real engine + the real
file-backed review queue against a throwaway workspace (no model call, no API budget):

  1. A consolidated-design gate: all three app leads + InfoSec must approve, AND any 2 of a 3-person
     reviewer pool, AND at least 3 distinct people total (min_distinct_approvers).
  2. Approvals arrive one at a time; the running tally is printed until quorum flips it to APPROVED.
  3. Structural enforcement: an approval by a non-member of the role is ignored.
  4. A second gate shows a single reject making the gate REJECTED.

Run it:

    uv run python packages/runtime/demos/multi_party_approval.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from swarmkit_runtime.governance._approval import (
    ApprovalPolicy,
    KOf,
    Resolution,
    Role,
    RoleRegistry,
    Rule,
    evaluate,
)
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import (
    collect_resolutions,
    open_gate,
    resolve_multiparty,
    role_task_item_id,
)

REGISTRY = RoleRegistry(
    roles={
        "oms-lead": Role("oms-lead", frozenset({"alice"}), frozenset({"design:approve"})),
        "web-lead": Role("web-lead", frozenset({"bob"}), frozenset({"design:approve"})),
        "mobile-lead": Role("mobile-lead", frozenset({"carol"}), frozenset({"design:approve"})),
        "infosec-lead": Role("infosec-lead", frozenset({"dana"}), frozenset({"security:approve"})),
        "rev-a": Role("rev-a", frozenset({"erin"}), frozenset({"design:approve"})),
        "rev-b": Role("rev-b", frozenset({"frank"}), frozenset({"design:approve"})),
        "rev-c": Role("rev-c", frozenset({"gita"}), frozenset({"design:approve"})),
    }
)

DESIGN_GATE = ApprovalPolicy(
    rules=(
        Rule("design:approve", ("oms-lead", "web-lead", "mobile-lead"), "all"),
        Rule("security:approve", ("infosec-lead",), "all"),
        Rule("design:approve", ("rev-a", "rev-b", "rev-c"), KOf(2)),
    ),
    min_distinct_approvers=3,
)


def _print_status(policy: ApprovalPolicy, resolutions: list[Resolution]) -> None:
    ev = evaluate(policy, REGISTRY, resolutions)
    outstanding = ", ".join(f"{t.role}" for t in ev.outstanding) or "—"
    print(
        f"    status={ev.status.value:<9} approvers={sorted(ev.distinct_approvers)}  "
        f"waiting_on=[{outstanding}]"
    )


async def scenario_approved() -> None:
    print(
        "① Consolidated-design gate — all app leads + InfoSec + any-2-of-3 reviewers, ≥3 distinct"
    )
    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        gov = MockGovernanceProvider()
        gate = "req-4821-design"
        open_gate(
            queue,
            gate_id=gate,
            topology_id="sdlc",
            agent_id="release-orchestrator",
            policy=DESIGN_GATE,
        )
        tasks_open = queue.list_pending()
        print(f"  gate opened → {len(tasks_open)} role-tasks:")
        for t in tasks_open:
            print(f"    · {t.output['role']} ({t.output['scope']})")

        # Approvals trickle in; a non-member attempt is ignored.
        script = [
            (0, "oms-lead", "alice"),
            (0, "web-lead", "bob"),
            (0, "mobile-lead", "eve"),  # eve is NOT a mobile-lead → ignored
            (0, "mobile-lead", "carol"),
            (1, "infosec-lead", "dana"),
            (2, "rev-a", "erin"),
            (2, "rev-b", "frank"),
        ]
        print("\n  approvals arriving:")
        for rule_idx, role, identity in script:
            queue.record_resolution(role_task_item_id(gate, rule_idx, role), "approved", identity)
            note = " (non-member — ignored)" if (role, identity) == ("mobile-lead", "eve") else ""
            print(f"  → {identity} approves as {role}{note}")
            _print_status(DESIGN_GATE, collect_resolutions(queue, gate_id=gate, policy=DESIGN_GATE))

        # The resolver returns immediately now that quorum is met.
        dec = await resolve_multiparty(
            gate_id=gate,
            policy=DESIGN_GATE,
            registry=REGISTRY,
            topology_id="sdlc",
            agent_id="release-orchestrator",
            governance=gov,
            review_queue=queue,
            max_wait_seconds=1,
        )
        print(f"\n  GATE DECISION: {dec.status.value.upper()}  approvers={sorted(dec.approvers)}")
        print(f"  audit events: {[e.event_type for e in gov.events]}")


async def scenario_rejected() -> None:
    print("\n② A single reject makes the gate REJECTED")
    policy = ApprovalPolicy(rules=(Rule("design:approve", ("oms-lead", "web-lead"), "all"),))
    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        gov = MockGovernanceProvider()
        gate = "req-4821-design-v2"
        open_gate(
            queue, gate_id=gate, topology_id="sdlc", agent_id="release-orchestrator", policy=policy
        )
        queue.record_resolution(role_task_item_id(gate, 0, "oms-lead"), "approved", "alice")
        queue.record_resolution(role_task_item_id(gate, 0, "web-lead"), "rejected", "bob")
        dec = await resolve_multiparty(
            gate_id=gate,
            policy=policy,
            registry=REGISTRY,
            topology_id="sdlc",
            agent_id="release-orchestrator",
            governance=gov,
            review_queue=queue,
            max_wait_seconds=1,
        )
        print(f"  GATE DECISION: {dec.status.value.upper()}  (bob rejected as web-lead)")


async def main() -> None:
    await scenario_approved()
    await scenario_rejected()
    print("\n✓ multi-party approval demo complete")


if __name__ == "__main__":
    asyncio.run(main())
