"""Demo: governed memory with temporal update-in-place (design/details/governed-memory.md, slice 1).

Replays a short timeline of observations about ONE subject whose preferred value changes, with a
duplicate restatement in between. Shows the canonical memory updated IN PLACE (one row, evolving),
a duplicate resolved as `reinforce` (no new row), the append-only change-log timeline, and a
point-in-time `as_of` read. Deterministic — no LLM, no keys, no server.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from swarmkit_runtime.governed_memory import GovernedMemoryStore, MemoryCandidate
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


def main() -> None:
    clock = _Clock()
    store = GovernedMemoryStore(create_engine("sqlite:///:memory:"), clock=clock)
    t0 = clock()

    print("A growing application's fact about one subject, over time:\n")
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
    print(
        f"\nCanonical current-state: {len(rows)} row — value='{m.value}', "
        f"reinforced x{m.reinforce_count}, confidence={m.confidence:.2f}"
    )

    print("\nAppend-only change-log (the fact's history):")
    for e in store.history("user:srijith", "preferred_tool_model"):
        before = e.before["value"] if e.before else "∅"
        print(f"  {e.timestamp[:10]}  {e.op:<9} {before} → {e.after['value']}")

    past = store.value_as_of("user:srijith", "preferred_tool_model", t0)
    assert past is not None
    print(f"\nas_of {t0.date()}: '{past.value}'   (now: '{m.value}') — history preserved")
    print("\n✓ one row, evolved in place; duplicate reinforced not duplicated; full log timeline.")


if __name__ == "__main__":
    main()
