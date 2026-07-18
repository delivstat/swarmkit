"""Multi-party approval gate resolution (review/_multiparty.py).

Exercises the runtime wiring around the pure engine: fan-out into per-role review
items, the bounded poll that drives resolutions through ``evaluate``, timeout
degradation, structural enforcement (non-member ignored), and audit events.
Time is injected (clock/sleep) so the tests are deterministic and fast.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

import pytest
from swarmkit_runtime.governance._approval import (
    ApprovalPolicy,
    GateStatus,
    KOf,
    Role,
    RoleRegistry,
    Rule,
)
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.review import FileReviewQueue, ReviewItem
from swarmkit_runtime.review._multiparty import (
    collect_resolutions,
    open_gate,
    resolve_multiparty,
    role_task_item_id,
)

GATE = "gate-1"

REGISTRY = RoleRegistry(
    roles={
        "oms-lead": Role("oms-lead", frozenset({"alice"}), frozenset({"design:approve"})),
        "web-lead": Role("web-lead", frozenset({"bob"}), frozenset({"design:approve"})),
        "infosec-lead": Role("infosec-lead", frozenset({"dana"}), frozenset({"security:approve"})),
    }
)


class FakeQueue:
    """Minimal in-memory review queue that lets a test resolve role-tasks by identity."""

    def __init__(self) -> None:
        self.items: dict[str, ReviewItem] = {}

    def submit(self, item: ReviewItem) -> None:
        self.items.setdefault(item.id, item)

    def get(self, item_id: str) -> ReviewItem | None:
        return self.items.get(item_id)

    def list_all(self) -> list[ReviewItem]:
        return list(self.items.values())

    def list_pending(self) -> list[ReviewItem]:
        return [i for i in self.items.values() if i.status == "pending"]

    # ReviewQueue protocol members (unused by these tests; present to satisfy the type).
    def resolve(self, item_id: str, status: str) -> bool:
        item = self.items.get(item_id)
        if item is None:
            return False
        self.items[item_id] = dataclasses.replace(item, status=status)  # type: ignore[arg-type]
        return True

    def answer_input(self, item_id: str, answer: str) -> bool:
        item = self.items.get(item_id)
        if item is None:
            return False
        self.items[item_id] = dataclasses.replace(item, status="approved", answer=answer)
        return True

    def resolve_task(
        self, rule_index: int, role: str, status: str, identity: str, gate_id: str = GATE
    ) -> None:
        iid = role_task_item_id(gate_id, rule_index, role)
        self.items[iid] = dataclasses.replace(self.items[iid], status=status, answer=identity)  # type: ignore[arg-type]


def seq_clock(step: float = 1.0) -> Callable[[], float]:
    t = [0.0]

    def clock() -> float:
        v = t[0]
        t[0] += step
        return v

    return clock


async def _noop_sleep(_: float) -> None:
    return None


def design_all(*roles: str) -> ApprovalPolicy:
    return ApprovalPolicy(rules=(Rule("design:approve", roles, "all"),))


# ---- fan-out -----------------------------------------------------------------


def test_open_gate_creates_one_item_per_role_task() -> None:
    q = FakeQueue()
    policy = ApprovalPolicy(
        rules=(
            Rule("design:approve", ("oms-lead", "web-lead"), "all"),
            Rule("security:approve", ("infosec-lead",), "all"),
        )
    )
    open_gate(q, gate_id=GATE, topology_id="t", agent_id="a", policy=policy)
    assert set(q.items) == {
        role_task_item_id(GATE, 0, "oms-lead"),
        role_task_item_id(GATE, 0, "web-lead"),
        role_task_item_id(GATE, 1, "infosec-lead"),
    }
    assert all(i.status == "pending" for i in q.items.values())


def test_collect_resolutions_maps_identity_and_outcome() -> None:
    q = FakeQueue()
    policy = design_all("oms-lead", "web-lead")
    open_gate(q, gate_id=GATE, topology_id="t", agent_id="a", policy=policy)
    q.resolve_task(0, "oms-lead", "approved", "alice")
    q.resolve_task(0, "web-lead", "rejected", "bob")
    res = collect_resolutions(q, gate_id=GATE, policy=policy)
    assert {(r.identity, r.role, r.outcome) for r in res} == {
        ("alice", "oms-lead", "approve"),
        ("bob", "web-lead", "reject"),
    }


# ---- resolution outcomes -----------------------------------------------------


@pytest.mark.asyncio
async def test_approved_when_all_roles_resolve() -> None:
    q = FakeQueue()
    gov = MockGovernanceProvider()
    policy = design_all("oms-lead", "web-lead")

    done = {"resolved": False}

    async def resolving_sleep(_: float) -> None:
        if not done["resolved"]:
            q.resolve_task(0, "oms-lead", "approved", "alice")
            q.resolve_task(0, "web-lead", "approved", "bob")
            done["resolved"] = True

    dec = await resolve_multiparty(
        gate_id=GATE,
        policy=policy,
        registry=REGISTRY,
        topology_id="t",
        agent_id="a",
        governance=gov,
        review_queue=q,
        clock=seq_clock(),
        sleep=resolving_sleep,
        max_wait_seconds=100,
    )
    assert dec.status is GateStatus.APPROVED
    assert dec.approvers == frozenset({"alice", "bob"})
    kinds = [e.event_type for e in gov.events]
    assert "approval.gate_opened" in kinds
    assert "approval.gate_resolved" in kinds


@pytest.mark.asyncio
async def test_rejected_when_a_role_rejects() -> None:
    q = FakeQueue()
    gov = MockGovernanceProvider()
    policy = design_all("oms-lead", "web-lead")

    async def resolving_sleep(_: float) -> None:
        q.resolve_task(0, "oms-lead", "rejected", "alice")

    dec = await resolve_multiparty(
        gate_id=GATE,
        policy=policy,
        registry=REGISTRY,
        topology_id="t",
        agent_id="a",
        governance=gov,
        review_queue=q,
        clock=seq_clock(),
        sleep=resolving_sleep,
        max_wait_seconds=100,
    )
    assert dec.status is GateStatus.REJECTED


@pytest.mark.asyncio
async def test_timeout_degrades_to_denial() -> None:
    q = FakeQueue()
    gov = MockGovernanceProvider()
    policy = design_all("oms-lead")
    dec = await resolve_multiparty(
        gate_id=GATE,
        policy=policy,
        registry=REGISTRY,
        topology_id="t",
        agent_id="a",
        governance=gov,
        review_queue=q,
        clock=seq_clock(step=1.0),
        sleep=_noop_sleep,
        max_wait_seconds=3,
    )
    assert dec.status is GateStatus.REJECTED
    assert "expired" in dec.reason


@pytest.mark.asyncio
async def test_non_member_resolution_is_ignored() -> None:
    q = FakeQueue()
    gov = MockGovernanceProvider()
    policy = design_all("oms-lead")

    async def resolving_sleep(_: float) -> None:
        # bob is not a member of oms-lead -> the engine ignores it -> stays pending -> timeout
        q.resolve_task(0, "oms-lead", "approved", "bob")

    dec = await resolve_multiparty(
        gate_id=GATE,
        policy=policy,
        registry=REGISTRY,
        topology_id="t",
        agent_id="a",
        governance=gov,
        review_queue=q,
        clock=seq_clock(step=1.0),
        sleep=resolving_sleep,
        max_wait_seconds=3,
    )
    assert dec.status is GateStatus.REJECTED  # never approved -> timed out


@pytest.mark.asyncio
async def test_k_of_reached_by_two_distinct() -> None:
    reg = RoleRegistry(
        roles={
            "rev-a": Role("rev-a", frozenset({"erin"}), frozenset({"design:approve"})),
            "rev-b": Role("rev-b", frozenset({"frank"}), frozenset({"design:approve"})),
            "rev-c": Role("rev-c", frozenset({"gita"}), frozenset({"design:approve"})),
        }
    )
    policy = ApprovalPolicy(rules=(Rule("design:approve", ("rev-a", "rev-b", "rev-c"), KOf(2)),))
    q = FakeQueue()
    gov = MockGovernanceProvider()

    async def resolving_sleep(_: float) -> None:
        q.resolve_task(0, "rev-a", "approved", "erin")
        q.resolve_task(0, "rev-b", "approved", "frank")

    dec = await resolve_multiparty(
        gate_id=GATE,
        policy=policy,
        registry=reg,
        topology_id="t",
        agent_id="a",
        governance=gov,
        review_queue=q,
        clock=seq_clock(),
        sleep=resolving_sleep,
        max_wait_seconds=100,
    )
    assert dec.status is GateStatus.APPROVED
    assert dec.approvers == frozenset({"erin", "frank"})


# ---- end-to-end through the real file-backed queue ---------------------------


@pytest.mark.asyncio
async def test_end_to_end_with_file_queue(tmp_path: Path) -> None:
    """Resolutions via FileReviewQueue.record_resolution (what the serve /resolve endpoint calls),
    including a reject that carries the resolver identity so the engine can verify membership."""
    q = FileReviewQueue(tmp_path)
    gov = MockGovernanceProvider()
    policy = design_all("oms-lead", "web-lead")

    async def resolving_sleep(_: float) -> None:
        q.record_resolution(role_task_item_id(GATE, 0, "oms-lead"), "approved", "alice")
        q.record_resolution(role_task_item_id(GATE, 0, "web-lead"), "approved", "bob")

    dec = await resolve_multiparty(
        gate_id=GATE,
        policy=policy,
        registry=REGISTRY,
        topology_id="t",
        agent_id="a",
        governance=gov,
        review_queue=q,
        clock=seq_clock(),
        sleep=resolving_sleep,
        max_wait_seconds=100,
    )
    assert dec.status is GateStatus.APPROVED
    assert dec.approvers == frozenset({"alice", "bob"})


@pytest.mark.asyncio
async def test_reject_with_identity_counts(tmp_path: Path) -> None:
    q = FileReviewQueue(tmp_path)
    gov = MockGovernanceProvider()
    policy = design_all("oms-lead", "web-lead")

    async def resolving_sleep(_: float) -> None:
        # a rejecter's identity must be recorded, else the engine can't verify eligibility
        q.record_resolution(role_task_item_id(GATE, 0, "oms-lead"), "rejected", "alice")

    dec = await resolve_multiparty(
        gate_id=GATE,
        policy=policy,
        registry=REGISTRY,
        topology_id="t",
        agent_id="a",
        governance=gov,
        review_queue=q,
        clock=seq_clock(),
        sleep=resolving_sleep,
        max_wait_seconds=100,
    )
    assert dec.status is GateStatus.REJECTED
