"""Value objects for governed memory (design/details/governed-memory.md).

Plain dataclasses — schema-shaped data the store reads/writes. ``MemoryCandidate`` is what an agent
proposes; ``Memory`` is a stored current-state row; ``ChangeLogEntry`` is one append-only mutation;
``WriteOutcome`` is what a governed write returns (which reconcile op ran, on which key).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

#: Every reconcile op. ``new`` / ``reinforce`` / ``update`` are decided deterministically (no LLM);
#: ``refine`` (merge) and ``contradict`` (quarantine + escalate) come from the reconcile decision
#: skill, which only ever runs on the ambiguous same-key-changed-value case.
ReconcileOp = Literal["new", "update", "reinforce", "refine", "contradict"]

#: The reconcile decision skill's verdict for a changed value — the ops that need judgement.
JudgeOp = Literal["update", "refine", "contradict"]

#: Memory types (design/details/governed-memory.md; mirrors the persistence skill's declared set).
MemoryType = Literal["semantic", "profile", "procedural", "episodic", "working"]


def memory_key(subject: str, attribute: str) -> str:
    """The stable reconciliation anchor for a fact: ``f"{subject}::{attribute}"``.

    A later observation about the same ``(subject, attribute)`` resolves to the same key, so it
    updates the existing memory in place rather than creating a new row.
    """
    return f"{subject}::{attribute}"


def content_hash(value: str) -> str:
    """sha256 of a memory's value — the exact-dedup signal (same key + same hash ⇒ reinforce)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryCandidate:
    """A memory an agent proposes to write. The store reconciles it against current memory."""

    subject: str
    attribute: str
    value: str
    type: MemoryType = "semantic"
    confidence: float = 1.0
    source: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return memory_key(self.subject, self.attribute)


@dataclass(frozen=True)
class Memory:
    """A stored current-state memory row."""

    key: str
    subject: str
    attribute: str
    value: str
    type: str
    confidence: float
    content_hash: str
    valid_from: str
    last_reinforced_at: str
    reinforce_count: int
    source: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    status: str = "active"


@dataclass(frozen=True)
class ChangeLogEntry:
    """One append-only mutation of a memory."""

    id: int
    memory_key: str
    op: str
    before: dict[str, Any] | None
    after: dict[str, Any]
    reason: str
    decided_by: str
    timestamp: str


@dataclass(frozen=True)
class QuarantineItem:
    """A parked contradiction awaiting a human/curator decision: a candidate the reconcile skill
    judged a ``contradict`` of the trusted ``current_value``."""

    id: int
    memory_key: str
    candidate: dict[str, Any]
    current_value: str
    reasoning: str
    status: str  # pending | accepted | rejected
    created_at: str
    resolved_at: str | None = None
    resolved_by: str | None = None


@dataclass(frozen=True)
class WriteOutcome:
    """The result of a governed write: which reconcile op ran, on which key, and the resulting row.

    ``changed`` is True when the memory's *value* changed (``new`` / ``update``) and False for a
    ``reinforce`` (recency/confidence bumped, value identical — no new row, no value change).
    """

    op: ReconcileOp
    memory_key: str
    memory: Memory
    changed: bool
