"""Confidence decay — a memory's *effective* confidence fades with time since last reinforced
(design/details/governed-memory.md, "Temporal model: recency, confidence, decay").

Stale facts rank *down* in retrieval without being deleted — the history stays; a memory that keeps
being observed (``reinforce``) resets its recency and stays strong, one that stops being observed
fades. Exponential half-life per memory ``type`` (``working`` memory forgets faster than
``semantic``); a fact reinforced within its half-life keeps most of its confidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from swarmkit_runtime.governed_memory._models import Memory

_SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True)
class DecayConfig:
    """Per-type half-lives (in days). A memory at exactly one half-life old has half its stored
    confidence as *effective* confidence. ``half_lives`` overrides ``default_half_life_days`` per
    memory type."""

    default_half_life_days: float = 90.0
    half_lives: dict[str, float] = field(default_factory=dict)

    def half_life(self, memory_type: str) -> float:
        return self.half_lives.get(memory_type, self.default_half_life_days)


def effective_confidence(memory: Memory, now: datetime, config: DecayConfig) -> float:
    """The memory's confidence discounted by exponential decay over the time since it was last
    reinforced. No decay for a future/zero age or a non-positive half-life (decay disabled)."""
    half_life = config.half_life(memory.type)
    if half_life <= 0:
        return memory.confidence
    try:
        last = datetime.fromisoformat(memory.last_reinforced_at)
    except ValueError:
        return memory.confidence
    age_days = (now - last).total_seconds() / _SECONDS_PER_DAY
    if age_days <= 0:
        return memory.confidence
    return float(memory.confidence * (0.5 ** (age_days / half_life)))
