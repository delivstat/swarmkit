"""The reconcile decision skill + quarantine + decay (design/details/governed-memory.md, slice 2).

A changed value on an existing key is judged: ``update`` (supersede), ``refine`` (merge), or
``contradict`` (leave the trusted memory untouched, quarantine the candidate for the curator). The
judge is injected as a fake; ``build_memory_reconciler`` is exercised against the governance
decision-skill seam. Confidence decays with recency so stale facts rank down.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from swarmkit_runtime.governance import DecisionSkillResult
from swarmkit_runtime.governed_memory import (
    DecayConfig,
    GovernedMemoryStore,
    Memory,
    MemoryCandidate,
    Reconciler,
    ReconcileRequest,
    ReconcileVerdict,
    build_memory_reconciler,
    effective_confidence,
)

pytestmark = pytest.mark.asyncio


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._t

    def advance(self, **kw: float) -> None:
        self._t += timedelta(**kw)


def _cand(value: str, **kw: object) -> MemoryCandidate:
    base: dict[str, object] = {"subject": "user:srijith", "attribute": "preferred_tool_model"}
    base.update(kw)
    return MemoryCandidate(value=value, **base)  # type: ignore[arg-type]


def _fixed(verdict: ReconcileVerdict) -> Reconciler:
    async def reconciler(_req: ReconcileRequest) -> ReconcileVerdict:
        return verdict

    return reconciler


# ── the three judged ops ────────────────────────────────────────────────────────────────────────
async def test_awrite_update_supersedes_in_place() -> None:
    store = GovernedMemoryStore(
        create_engine("sqlite:///:memory:"), reconciler=_fixed(ReconcileVerdict(op="update"))
    )
    store.write(_cand("Kimi K2.5"))
    out = await store.awrite(_cand("DeepSeek V3"))
    assert out.op == "update" and out.changed is True
    got = store.get("user:srijith", "preferred_tool_model")
    assert got is not None and got.value == "DeepSeek V3"


async def test_awrite_refine_applies_merged_value_logged_as_refine() -> None:
    verdict = ReconcileVerdict(op="refine", merged_value="Kimi K2.5 (tools), DeepSeek V3 (writing)")
    store = GovernedMemoryStore(create_engine("sqlite:///:memory:"), reconciler=_fixed(verdict))
    store.write(_cand("Kimi K2.5"))
    out = await store.awrite(_cand("DeepSeek V3"))
    assert out.op == "refine"
    got = store.get("user:srijith", "preferred_tool_model")
    assert got is not None and got.value == "Kimi K2.5 (tools), DeepSeek V3 (writing)"
    assert store.history("user:srijith", "preferred_tool_model")[-1].op == "refine"


async def test_awrite_contradict_quarantines_and_leaves_trusted_memory() -> None:
    store = GovernedMemoryStore(
        create_engine("sqlite:///:memory:"),
        reconciler=_fixed(
            ReconcileVerdict(op="contradict", reasoning="conflicts with a firm pref")
        ),
    )
    store.write(_cand("Kimi K2.5", confidence=0.95))
    out = await store.awrite(_cand("hates all models"))

    assert out.op == "contradict" and out.changed is False
    # the trusted canonical memory is UNTOUCHED
    got = store.get("user:srijith", "preferred_tool_model")
    assert got is not None and got.value == "Kimi K2.5"
    # the candidate is parked for the curator
    q = store.list_quarantine()
    assert len(q) == 1
    assert q[0].candidate["value"] == "hates all models"
    assert q[0].current_value == "Kimi K2.5" and q[0].status == "pending"
    # the contradiction is on the append-only log
    assert store.history("user:srijith", "preferred_tool_model")[-1].op == "contradict"


# ── curator resolves a quarantined contradiction (the one hard human gate) ───────────────────────
async def test_resolve_quarantine_accept_applies_update() -> None:
    store = GovernedMemoryStore(
        create_engine("sqlite:///:memory:"),
        reconciler=_fixed(ReconcileVerdict(op="contradict")),
    )
    store.write(_cand("Kimi K2.5"))
    await store.awrite(_cand("DeepSeek V3"))
    qid = store.list_quarantine()[0].id

    result = store.resolve_quarantine(qid, accept=True, resolved_by="curator:alice")
    assert result is not None and result.op == "update"
    got = store.get("user:srijith", "preferred_tool_model")
    assert got is not None and got.value == "DeepSeek V3"  # curator affirmed the supersede
    assert store.list_quarantine() == []  # no longer pending
    assert store.list_quarantine(status="accepted")[0].resolved_by == "curator:alice"


async def test_resolve_quarantine_reject_discards() -> None:
    store = GovernedMemoryStore(
        create_engine("sqlite:///:memory:"),
        reconciler=_fixed(ReconcileVerdict(op="contradict")),
    )
    store.write(_cand("Kimi K2.5"))
    await store.awrite(_cand("nonsense"))
    qid = store.list_quarantine()[0].id

    assert store.resolve_quarantine(qid, accept=False, resolved_by="curator:bob") is None
    got = store.get("user:srijith", "preferred_tool_model")
    assert got is not None and got.value == "Kimi K2.5"  # trusted value stands
    assert store.list_quarantine(status="rejected")[0].id == qid
    # resolving an already-resolved item is a no-op
    assert store.resolve_quarantine(qid, accept=True, resolved_by="curator:bob") is None


async def test_awrite_without_reconciler_falls_back_to_deterministic_update() -> None:
    store = GovernedMemoryStore(create_engine("sqlite:///:memory:"))  # no reconciler
    store.write(_cand("Kimi K2.5"))
    out = await store.awrite(_cand("DeepSeek V3"))
    assert out.op == "update"  # same as write()
    assert store.list_quarantine() == []


# ── the governance decision-skill adapter ────────────────────────────────────────────────────────
class _FakeGov:
    def __init__(self, result: DecisionSkillResult) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    async def evaluate_decision_skill(self, **kw: object) -> DecisionSkillResult:
        self.calls.append(kw)
        return self._result


async def test_build_memory_reconciler_maps_explicit_op_and_verdict() -> None:
    cur = Memory(
        key="user:srijith::preferred_tool_model",
        subject="user:srijith",
        attribute="preferred_tool_model",
        value="Kimi K2.5",
        type="semantic",
        confidence=0.9,
        content_hash="x",
        valid_from="t",
        last_reinforced_at="t",
        reinforce_count=1,
    )
    req = ReconcileRequest(candidate=_cand("DeepSeek V3"), current=cur)

    # explicit raw op wins (with a merged value)
    gov = _FakeGov(
        DecisionSkillResult(
            skill_id="memory-reconcile",
            verdict="pass",
            confidence=0.8,
            reasoning="r",
            raw={"op": "refine", "merged_value": "both"},
        )
    )
    r = build_memory_reconciler(governance=gov, skill_id="memory-reconcile", agent_id="curator")
    v = await r(req)
    assert v.op == "refine" and v.merged_value == "both"
    assert gov.calls[0]["skill_id"] == "memory-reconcile"

    # no explicit op → the pass/needs-revision/fail verdict maps
    for verdict, expected in [
        ("pass", "update"),
        ("needs-revision", "refine"),
        ("fail", "contradict"),
    ]:
        gov2 = _FakeGov(
            DecisionSkillResult(skill_id="s", verdict=verdict, confidence=0.5, reasoning="")  # type: ignore[arg-type]
        )
        rr = build_memory_reconciler(governance=gov2, skill_id="s", agent_id="a")
        assert (await rr(req)).op == expected


# ── confidence decay ─────────────────────────────────────────────────────────────────────────────
async def test_effective_confidence_halves_at_one_half_life() -> None:
    now = datetime(2026, 4, 1, tzinfo=UTC)  # 90 days after last reinforced
    m = Memory(
        key="k",
        subject="s",
        attribute="a",
        value="v",
        type="semantic",
        confidence=0.8,
        content_hash="h",
        valid_from="t",
        last_reinforced_at=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        reinforce_count=1,
    )
    cfg = DecayConfig(default_half_life_days=90.0)
    assert effective_confidence(m, now, cfg) == pytest.approx(0.4)  # halved at one half-life


async def test_search_decay_ranks_fresh_over_stale() -> None:
    clock = _Clock()
    store = GovernedMemoryStore(
        create_engine("sqlite:///:memory:"),
        clock=clock,
        decay=DecayConfig(default_half_life_days=30.0),
    )
    store.write(_cand("stale", subject="s", attribute="old", confidence=0.95))
    clock.advance(days=120)  # 'stale' is now 4 half-lives old → effective ~0.06
    store.write(_cand("fresh", subject="s", attribute="new", confidence=0.7))

    ranked = store.search()
    assert [m.value for m in ranked] == [
        "fresh",
        "stale",
    ]  # decay demotes the older, higher-conf one
