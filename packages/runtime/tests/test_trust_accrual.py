"""Trust accrual (§6.2.3 / decision 6): repeated operator approvals of a relayed capability accrue
toward an allowlist-changeset proposal; one denial resets + blocks the pair. Covers the store's
semantics and its wiring into the relay orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from swarmkit_runtime.executors import ExecApprovalRequested
from swarmkit_runtime.governance import AuditEvent, PolicyDecision
from swarmkit_runtime.langgraph_compiler._relay import resolve_relay
from swarmkit_runtime.review import ReviewItem
from swarmkit_runtime.trust import TrustStore

# --- store semantics ----------------------------------------------------------------------------


def test_proposal_emitted_exactly_at_threshold(tmp_path: Path) -> None:
    store = TrustStore(tmp_path, threshold=3)
    assert store.record("coder", "Bash(npm test)", True) is None  # 1
    assert store.record("coder", "Bash(npm test)", True) is None  # 2
    proposal = store.record("coder", "Bash(npm test)", True)  # 3 → crosses
    assert proposal is not None
    assert (proposal.archetype, proposal.capability, proposal.approvals) == (
        "coder",
        "Bash(npm test)",
        3,
    )
    # Not re-proposed on the next approval — the proposal is emitted once.
    assert store.record("coder", "Bash(npm test)", True) is None
    assert [(p.archetype, p.capability) for p in store.proposals()] == [("coder", "Bash(npm test)")]


def test_denial_resets_and_blocks(tmp_path: Path) -> None:
    store = TrustStore(tmp_path, threshold=2)
    store.record("coder", "Bash(rm -rf)", True)  # 1
    assert store.record("coder", "Bash(rm -rf)", False) is None  # deliberate no → reset + block
    # Now blocked: even reaching the threshold does not propose.
    assert store.record("coder", "Bash(rm -rf)", True) is None  # 1 (post-reset)
    assert store.record("coder", "Bash(rm -rf)", True) is None  # 2 — but blocked
    assert store.proposals() == []


def test_clear_lifts_block_and_pair_can_accrue_again(tmp_path: Path) -> None:
    store = TrustStore(tmp_path, threshold=2)
    store.record("coder", "Bash(deploy)", False)  # block it
    assert store.clear("coder", "Bash(deploy)") is True
    store.record("coder", "Bash(deploy)", True)  # 1
    assert store.record("coder", "Bash(deploy)", True) is not None  # 2 → proposes again
    assert store.clear("coder", "unknown-cap") is False


def test_apply_marks_proposal_and_drops_it_from_pending(tmp_path: Path) -> None:
    store = TrustStore(tmp_path, threshold=1)
    store.record("coder", "Bash(ls)", True)  # proposes immediately
    assert len(store.proposals()) == 1
    assert store.apply("coder", "Bash(ls)") is True
    assert store.proposals() == []  # applied → no longer pending
    assert store.apply("coder", "never-proposed") is False


def test_pairs_are_independent(tmp_path: Path) -> None:
    store = TrustStore(tmp_path, threshold=1)
    store.record("coder", "Bash(a)", True)
    store.record("writer", "Bash(a)", True)  # same capability, different archetype
    assert {(p.archetype, p.capability) for p in store.proposals()} == {
        ("coder", "Bash(a)"),
        ("writer", "Bash(a)"),
    }


# --- relay hook ---------------------------------------------------------------------------------


class _Gov:
    """Minimal GovernanceProvider double: records audit events; policy always denies (so the request
    falls through to the operator inbox)."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def evaluate_action(self, **_: object) -> PolicyDecision:
        return PolicyDecision(allowed=False, reason="not-allowlisted", tier=1)

    async def record_event(self, event: AuditEvent) -> None:
        self.events.append(event)


def _request() -> ExecApprovalRequested:
    return ExecApprovalRequested(run_id="r1", capability="Bash(npm test)", rationale="run tests")


class _DecidedQueue:
    """A review queue that reports every submitted item as already decided — so the bounded wait
    returns on the first poll, exercising the operator branch without real time."""

    def __init__(self, status: str) -> None:
        self._status = status

    def submit(self, item: ReviewItem) -> None:
        self._item = item

    def get(self, item_id: str) -> ReviewItem | None:
        return ReviewItem(
            id=item_id,
            topology_id="t",
            agent_id="coder",
            skill_id="harness-approval",
            output={},
            verdict={},
            reason="",
            timestamp=datetime.now(tz=UTC),
            status=self._status,  # type: ignore[arg-type]
        )

    def list_pending(self) -> list[ReviewItem]:
        return []

    def resolve(self, item_id: str, status: str) -> bool:
        return True

    def answer_input(self, item_id: str, answer: str) -> bool:
        return True


async def _approve_once(tmp_path: Path, store: TrustStore, *, granted: bool) -> list[AuditEvent]:
    """Drive one relay resolution to an immediate operator decision."""
    gov = _Gov()
    await resolve_relay(
        _request(),
        agent_id="coder",
        topology_id="t",
        governance=gov,  # type: ignore[arg-type]
        review_queue=_DecidedQueue("approved" if granted else "rejected"),
        trust=store,
        archetype="coding-worker",
        max_wait_seconds=5.0,
        poll_interval=0.0,
    )
    return gov.events


@pytest.mark.asyncio
async def test_relay_accrues_operator_approvals_and_audits_proposal(tmp_path: Path) -> None:
    store = TrustStore(tmp_path, threshold=2)
    await _approve_once(tmp_path, store, granted=True)  # 1 — no proposal yet
    assert not _proposals(store)
    events = await _approve_once(tmp_path, store, granted=True)  # 2 — crosses
    assert _proposals(store) == [("coding-worker", "Bash(npm test)")]
    proposed = [e for e in events if e.event_type == "trust.changeset_proposed"]
    assert len(proposed) == 1
    assert proposed[0].payload["archetype"] == "coding-worker"
    assert proposed[0].payload["capability"] == "Bash(npm test)"


@pytest.mark.asyncio
async def test_relay_denial_blocks_the_pair(tmp_path: Path) -> None:
    store = TrustStore(tmp_path, threshold=1)
    await _approve_once(tmp_path, store, granted=False)  # deliberate no → block
    await _approve_once(tmp_path, store, granted=True)  # would cross, but blocked
    assert _proposals(store) == []


@pytest.mark.asyncio
async def test_relay_without_archetype_does_not_accrue(tmp_path: Path) -> None:
    gov = _Gov()
    store = TrustStore(tmp_path, threshold=1)
    await resolve_relay(
        _request(),
        agent_id="coder",
        topology_id="t",
        governance=gov,  # type: ignore[arg-type]
        review_queue=_DecidedQueue("approved"),
        trust=store,
        archetype=None,  # no archetype ⇒ no accrual
        max_wait_seconds=5.0,
        poll_interval=0.0,
    )
    assert _proposals(store) == []


def _proposals(store: TrustStore) -> list[tuple[str, str]]:
    return [(p.archetype, p.capability) for p in store.proposals()]
