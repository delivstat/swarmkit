"""Deterministic reconcile — the zero-LLM half of the governed write path
(design/details/governed-memory.md, "The governed write path" — the deterministic guardrails).

This slice resolves the cases that need **no** model call, which is the majority: a fact on a new
key is ``new``; an identical restatement of an existing fact is ``reinforce`` (no new row); a
changed value on an existing key is ``update`` (evolve in place). The discrimination that *does*
need judgement — is a changed value a legitimate ``update``, a ``refine``, or a ``contradict`` to
quarantine + escalate? — is the reconcile **decision skill**'s job, layered on top in the next
slice. Until then a changed value deterministically ``update``s in place, which is the update-in-
place spine the design is built around; contradiction handling is explicitly deferred, not faked.
"""

from __future__ import annotations

from dataclasses import dataclass

from swarmkit_runtime.governed_memory._models import (
    Memory,
    MemoryCandidate,
    ReconcileOp,
    content_hash,
)


@dataclass(frozen=True)
class ReconcileDecision:
    """The deterministic verdict: which op to apply, on which key, against which current memory."""

    op: ReconcileOp
    memory_key: str
    current: Memory | None


def reconcile(candidate: MemoryCandidate, current: Memory | None) -> ReconcileDecision:
    """Decide the reconcile op deterministically (no LLM).

    * ``current is None`` → ``new`` (insert).
    * same key, identical value (content-hash match) → ``reinforce`` (bump recency/confidence).
    * same key, changed value → ``update`` (supersede the value in place; log the change).
    """
    key = candidate.key
    if current is None:
        return ReconcileDecision(op="new", memory_key=key, current=None)
    if current.content_hash == content_hash(candidate.value):
        return ReconcileDecision(op="reinforce", memory_key=key, current=current)
    return ReconcileDecision(op="update", memory_key=key, current=current)
