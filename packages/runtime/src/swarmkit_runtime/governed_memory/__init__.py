"""Governed memory — governed writes with temporal update-in-place.

See design/details/governed-memory.md. This slice ships the deterministic core: the
``GovernedMemoryStore`` (canonical current-state + append-only change-log) and the zero-LLM
``reconcile`` (new / update / reinforce). The reconcile *decision skill* (refine / contradict →
quarantine + escalate) layers on top in a later slice.
"""

from __future__ import annotations

from swarmkit_runtime.governed_memory._models import (
    ChangeLogEntry,
    Memory,
    MemoryCandidate,
    ReconcileOp,
    WriteOutcome,
    content_hash,
    memory_key,
)
from swarmkit_runtime.governed_memory._reconcile import ReconcileDecision, reconcile
from swarmkit_runtime.governed_memory._store import GovernedMemoryStore

__all__ = [
    "ChangeLogEntry",
    "GovernedMemoryStore",
    "Memory",
    "MemoryCandidate",
    "ReconcileDecision",
    "ReconcileOp",
    "WriteOutcome",
    "content_hash",
    "memory_key",
    "reconcile",
]
