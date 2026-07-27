"""Reconcile — decide what a candidate memory does to the current memory
(design/details/governed-memory.md, "The governed write path").

Two layers, cheap-first:

* **Deterministic** (``reconcile``, no LLM — the majority of writes): a fact on a new key is
  ``new``; an identical restatement is ``reinforce`` (no new row); a **changed** value on an
  existing key is the one case that needs judgement.
* **The reconcile decision skill** (``build_memory_reconciler``, LLM — only the changed-value
  case): is the change a legitimate ``update``, a ``refine`` (merge detail into the existing
  memory), or a ``contradict`` (conflicts with a trusted memory → quarantine + escalate to the
  human/curator)? When no reconciler is wired the store falls back to a deterministic ``update``,
  which is the update-in-place spine — contradiction handling is opt-in, never silently skipped.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from swarmkit_runtime.governed_memory._models import (
    JudgeOp,
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
    * same key, changed value → ``update`` (the case the decision skill refines into
      update/refine/contradict; ``update`` is the deterministic fallback when none is wired).
    """
    key = candidate.key
    if current is None:
        return ReconcileDecision(op="new", memory_key=key, current=None)
    if current.content_hash == content_hash(candidate.value):
        return ReconcileDecision(op="reinforce", memory_key=key, current=current)
    return ReconcileDecision(op="update", memory_key=key, current=current)


@dataclass(frozen=True)
class ReconcileRequest:
    """What the reconcile decision skill judges: a changed-value candidate against the current
    memory for its key (and, later, near-neighbour memories for cross-fact context)."""

    candidate: MemoryCandidate
    current: Memory
    neighbors: list[Memory] = field(default_factory=list)


@dataclass(frozen=True)
class ReconcileVerdict:
    """The decision skill's verdict for a changed value. ``merged_value`` carries the refined text
    on a ``refine`` (falls back to the candidate's value when absent)."""

    op: JudgeOp
    reasoning: str = ""
    confidence: float = 0.0
    merged_value: str | None = None


#: The reconcile decision-skill seam. Async (it wraps an audited LLM decision). Injected into the
#: store; only invoked on a changed-value candidate. Fakes stand in for it in tests.
Reconciler = Callable[[ReconcileRequest], Awaitable[ReconcileVerdict]]


def build_memory_reconciler(*, governance: Any, skill_id: str, agent_id: str) -> Reconciler:
    """Bind the reconcile layer to the governance decision-skill seam (design §8).

    Runs ``governance.evaluate_decision_skill`` over the candidate vs the current memory and maps
    its result to a :class:`ReconcileVerdict`. The skill's structured ``raw`` output drives the op
    (``raw["op"]`` in update/refine/contradict, ``raw["merged_value"]`` for a refine); when the
    skill emits no explicit op, its pass/needs-revision/fail verdict maps to update/refine/
    contradict — so any conformant decision skill works, a reconcile-aware one gets finer control.
    """

    async def reconciler(req: ReconcileRequest) -> ReconcileVerdict:
        content = (
            f"CURRENT MEMORY [{req.current.key}] (confidence {req.current.confidence:.2f}):\n"
            f"{req.current.value}\n\n"
            f"PROPOSED NEW VALUE:\n{req.candidate.value}"
        )
        result = await governance.evaluate_decision_skill(
            skill_id=skill_id,
            trigger="memory_reconcile",
            agent_id=agent_id,
            content=content,
            context={"memory_key": req.current.key, "attribute": req.candidate.attribute},
        )
        raw = result.raw if isinstance(result.raw, dict) else {}
        op = _op_from_result(raw.get("op"), result.verdict)
        merged = raw.get("merged_value")
        return ReconcileVerdict(
            op=op,
            reasoning=result.reasoning,
            confidence=result.confidence,
            merged_value=str(merged) if merged is not None else None,
        )

    return reconciler


_VERDICT_TO_OP: dict[str, JudgeOp] = {
    "pass": "update",
    "needs-revision": "refine",
    "fail": "contradict",
}


def _op_from_result(explicit: Any, verdict: str) -> JudgeOp:
    """Prefer the skill's explicit reconcile op; else map its pass/needs-revision/fail verdict.

    An unrecognised value fails safe to ``contradict`` — an unclear judgement parks for a human
    rather than silently overwriting a trusted memory."""
    if explicit in ("update", "refine", "contradict"):
        return cast(JudgeOp, explicit)
    return _VERDICT_TO_OP.get(verdict, "contradict")
