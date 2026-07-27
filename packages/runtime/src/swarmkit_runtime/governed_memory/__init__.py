"""Governed memory — governed writes with temporal update-in-place.

See design/details/governed-memory.md. The ``GovernedMemoryStore`` reconciles every write against
current memory (canonical current-state + append-only change-log). Deterministic ``write`` handles
new / reinforce / update with no LLM; async ``awrite`` adds the reconcile decision skill for a
changed value — refine (merge) or contradict (quarantine + escalate to the curator) — plus
confidence decay in retrieval ranking.
"""

from __future__ import annotations

from swarmkit_runtime.governed_memory._decay import DecayConfig, effective_confidence
from swarmkit_runtime.governed_memory._models import (
    ChangeLogEntry,
    JudgeOp,
    Memory,
    MemoryCandidate,
    QuarantineItem,
    ReconcileOp,
    WriteOutcome,
    content_hash,
    memory_key,
)
from swarmkit_runtime.governed_memory._reconcile import (
    ReconcileDecision,
    Reconciler,
    ReconcileRequest,
    ReconcileVerdict,
    build_memory_reconciler,
    reconcile,
)
from swarmkit_runtime.governed_memory._store import GovernedMemoryStore

__all__ = [
    "ChangeLogEntry",
    "DecayConfig",
    "GovernedMemoryStore",
    "JudgeOp",
    "Memory",
    "MemoryCandidate",
    "QuarantineItem",
    "ReconcileDecision",
    "ReconcileOp",
    "ReconcileRequest",
    "ReconcileVerdict",
    "Reconciler",
    "WriteOutcome",
    "build_memory_reconciler",
    "content_hash",
    "effective_confidence",
    "memory_key",
    "reconcile",
]
