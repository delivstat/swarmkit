"""GovernedMemoryStore — the deterministic slice (design/details/governed-memory.md).

Update-in-place over a canonical current-state table + an append-only change-log: a growing
application's facts evolve on ONE row (new / update / reinforce), and only a genuinely new key
creates a row. History and point-in-time reads come from the append-only log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from swarmkit_runtime.governed_memory import (
    GovernedMemoryStore,
    MemoryCandidate,
    reconcile,
)
from swarmkit_runtime.governed_memory._models import content_hash
from swarmkit_runtime.governed_memory._tables import change_log, memory


class _Clock:
    """A controllable monotonic clock so temporal behaviour is deterministic."""

    def __init__(self) -> None:
        self._t = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._t

    def advance(self, **kw: float) -> None:
        self._t += timedelta(**kw)


def _store(clock: _Clock | None = None) -> GovernedMemoryStore:
    return GovernedMemoryStore(create_engine("sqlite:///:memory:"), clock=clock)


def _cand(value: str, **kw: object) -> MemoryCandidate:
    base: dict[str, object] = {"subject": "user:srijith", "attribute": "preferred_tool_model"}
    base.update(kw)
    return MemoryCandidate(value=value, **base)  # type: ignore[arg-type]


# ── reconcile (pure) ───────────────────────────────────────────────────────────────────────────
def test_reconcile_new_reinforce_update() -> None:
    c = _cand("Kimi K2.5")
    assert reconcile(c, None).op == "new"

    store = _store()
    m = store.write(c).memory
    assert reconcile(_cand("Kimi K2.5"), m).op == "reinforce"  # identical value
    assert reconcile(_cand("DeepSeek V3"), m).op == "update"  # changed value


# ── update-in-place: one row evolves, only new keys create rows ──────────────────────────────────
def test_update_in_place_keeps_one_row() -> None:
    clock = _Clock()
    store = _store(clock)

    out1 = store.write(_cand("Kimi K2.5"))
    assert out1.op == "new" and out1.changed is True

    clock.advance(days=1)
    out2 = store.write(_cand("DeepSeek V3"))  # same key, changed value → update in place
    assert out2.op == "update" and out2.changed is True

    # exactly one canonical row for the key; it holds the LATEST value.
    with store.engine.connect() as conn:
        rows = conn.execute(select(memory)).mappings().all()
    assert len(rows) == 1
    got = store.get("user:srijith", "preferred_tool_model")
    assert got is not None and got.value == "DeepSeek V3"

    # a genuinely different key DOES create a new row.
    store.write(_cand("dark", subject="user:srijith", attribute="theme"))
    with store.engine.connect() as conn:
        assert len(conn.execute(select(memory)).mappings().all()) == 2


def test_reinforce_bumps_confidence_and_recency_without_new_row() -> None:
    clock = _Clock()
    store = _store(clock)
    store.write(_cand("Kimi K2.5", confidence=0.8))

    clock.advance(hours=2)
    out = store.write(_cand("Kimi K2.5"))  # identical value
    assert out.op == "reinforce" and out.changed is False

    m = store.get("user:srijith", "preferred_tool_model")
    assert m is not None
    assert m.reinforce_count == 2
    assert m.confidence == pytest.approx(0.85)  # 0.8 + 0.05 step
    assert m.last_reinforced_at == clock().isoformat()  # recency advanced
    with store.engine.connect() as conn:  # still one row
        assert len(conn.execute(select(memory)).mappings().all()) == 1


# ── append-only change-log + temporal reads ─────────────────────────────────────────────────────
def test_history_is_append_only_and_ordered() -> None:
    clock = _Clock()
    store = _store(clock)
    store.write(_cand("Kimi K2.5"))
    clock.advance(days=1)
    store.write(_cand("Kimi K2.5"))  # reinforce
    clock.advance(days=1)
    store.write(_cand("DeepSeek V3"))  # update

    hist = store.history("user:srijith", "preferred_tool_model")
    assert [e.op for e in hist] == ["new", "reinforce", "update"]
    assert hist[0].before is None and hist[0].after["value"] == "Kimi K2.5"
    before2 = hist[2].before
    assert before2 is not None and before2["value"] == "Kimi K2.5"
    assert hist[2].after["value"] == "DeepSeek V3"
    # the log is content-hash faithful — every after-state carries its value hash.
    assert hist[2].after["content_hash"] == content_hash("DeepSeek V3")


def test_value_as_of_reconstructs_past_state() -> None:
    clock = _Clock()
    store = _store(clock)
    t0 = clock()
    store.write(_cand("Kimi K2.5"))
    clock.advance(days=10)
    t1 = clock()
    store.write(_cand("DeepSeek V3"))  # update

    sub, attr = "user:srijith", "preferred_tool_model"
    # before anything existed
    assert store.value_as_of(sub, attr, t0 - timedelta(days=1)) is None
    # at t0 the fact was the original value
    at_t0 = store.value_as_of(sub, attr, t0)
    assert at_t0 is not None and at_t0.value == "Kimi K2.5"
    # just before the update it was still the original
    mid = store.value_as_of(sub, attr, t1 - timedelta(days=1))
    assert mid is not None and mid.value == "Kimi K2.5"
    # at/after the update it is the new value; the current row agrees
    at_t1 = store.value_as_of(sub, attr, t1)
    assert at_t1 is not None and at_t1.value == "DeepSeek V3"
    now = store.get(sub, attr)
    assert now is not None and now.value == "DeepSeek V3"


def test_change_log_has_no_update_or_delete_surface() -> None:
    # The store exposes no method that mutates a logged entry — the audit invariant (§8.3).
    surface = {n for n in dir(GovernedMemoryStore) if not n.startswith("_")}
    assert not {"update_change", "delete_change", "edit_log", "purge_log"} & surface
    # and writes only ever INSERT into the log (id strictly increases, never reused).
    store = _store()
    store.write(_cand("a"))
    store.write(_cand("b"))  # update
    with store.engine.connect() as conn:
        ids = list(conn.execute(select(change_log.c.id).order_by(change_log.c.id)).scalars())
    assert ids == sorted(set(ids)) and len(ids) == 2


# ── search ───────────────────────────────────────────────────────────────────────────────────
def test_search_ranks_by_confidence_then_recency_and_filters() -> None:
    clock = _Clock()
    store = _store(clock)
    store.write(_cand("Kimi K2.5", confidence=0.9))
    store.write(_cand("light", subject="user:srijith", attribute="theme", confidence=0.6))
    store.write(
        _cand(
            "weekly",
            subject="user:srijith",
            attribute="report_cadence",
            type="profile",
            confidence=0.7,
        )
    )

    ranked = store.search()
    assert ranked[0].value == "Kimi K2.5"  # highest confidence first
    # substring query narrows
    assert [m.attribute for m in store.search("theme")] == ["theme"]
    # type filter
    assert [m.type for m in store.search(types=["profile"])] == ["profile"]
