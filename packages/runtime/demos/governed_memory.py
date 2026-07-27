"""Demo: governed memory — update-in-place + the reconcile decision skill
(design/details/governed-memory.md, slices 1 and 2).

Scene 1 (deterministic): a fact evolves IN PLACE over time — new / reinforce / update on one row,
append-only change-log, point-in-time `as_of`.
Scene 2 (the judge): a changed value is judged — a legit change `update`s; a conflicting one is a
`contradict` → parked in quarantine (trusted memory untouched) → the curator accepts/rejects.

Deterministic — the reconcile judge is a fake here (no LLM, no keys, no server).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from swarmkit_runtime.governed_memory import (
    GovernedMemoryStore,
    MemoryCandidate,
    ReconcileRequest,
    ReconcileVerdict,
)
from swarmkit_runtime.governed_memory._tables import memory


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._t

    def advance(self, **kw: float) -> None:
        self._t += timedelta(**kw)


def _cand(value: str) -> MemoryCandidate:
    return MemoryCandidate(subject="user:srijith", attribute="preferred_tool_model", value=value)


async def _judge(req: ReconcileRequest) -> ReconcileVerdict:
    """Stand-in for the reconcile decision skill: a value that reads as a firm reversal contradicts
    a high-confidence memory; anything else is a legitimate update."""
    if "never" in req.candidate.value.lower() and req.current.confidence >= 0.9:
        return ReconcileVerdict(op="contradict", reasoning="reverses a firmly-held preference")
    return ReconcileVerdict(op="update", reasoning="ordinary preference change")


def _scene1() -> None:
    clock = _Clock()
    store = GovernedMemoryStore(create_engine("sqlite:///:memory:"), clock=clock)
    t0 = clock()

    print("Scene 1 — a fact about one subject, over time (deterministic):\n")
    for value in ("Kimi K2.5", "Kimi K2.5", "DeepSeek V3"):
        out = store.write(_cand(value))
        note = {
            "new": "created",
            "reinforce": "identical — reinforced, NO new row",
            "update": "changed — updated IN PLACE",
        }[out.op]
        print(f"  {clock().date()}  observe '{value:<12}' → {out.op:<9} ({note})")
        clock.advance(days=5)

    with store.engine.connect() as conn:
        rows = conn.execute(select(memory)).mappings().all()
    m = store.get("user:srijith", "preferred_tool_model")
    assert m is not None
    print(f"\n  canonical: {len(rows)} row — value='{m.value}', reinforced x{m.reinforce_count}")
    past = store.value_as_of("user:srijith", "preferred_tool_model", t0)
    assert past is not None
    print(f"  as_of {t0.date()}: '{past.value}'  (now: '{m.value}') — history preserved\n")


async def _scene2() -> None:
    store = GovernedMemoryStore(create_engine("sqlite:///:memory:"), reconciler=_judge)
    print("Scene 2 — a changed value is judged (the reconcile decision skill):\n")

    store.write(_cand("Kimi K2.5"))
    for _ in range(3):
        store.write(_cand("Kimi K2.5"))  # reinforce → high confidence, a firmly-held preference
    print("  memory: 'Kimi K2.5' (reinforced, high confidence)")

    out = await store.awrite(_cand("never use Kimi again"))  # reads as a reversal → contradict
    q = store.list_quarantine()[0]
    print(f"  observe 'never use Kimi again' → {out.op} → QUARANTINED (trusted memory untouched)")
    m = store.get("user:srijith", "preferred_tool_model")
    assert m is not None
    print(
        f"    canonical still: '{m.value}'   ·   curator queue: {len(store.list_quarantine())} item"
    )

    result = store.resolve_quarantine(q.id, accept=False, resolved_by="curator:srijith")
    print(f"  curator REJECTS the contradiction → {result} (discarded; trusted value stands)")

    out2 = await store.awrite(_cand("DeepSeek V3"))  # ordinary change → update
    m2 = store.get("user:srijith", "preferred_tool_model")
    assert m2 is not None
    print(f"  observe 'DeepSeek V3' → {out2.op} → updated in place to '{m2.value}'\n")


async def main() -> None:
    _scene1()
    await _scene2()
    print("✓ facts evolve in place; contradictions park for the curator, never overwrite silently.")


if __name__ == "__main__":
    asyncio.run(main())
