"""Compiler hook — route an agent's proposed memories through the governed write path
(design/details/governed-memory.md, "API shape": the persistence skill an agent calls).

When an agent carries the ``governed-memory`` persistence skill, the compiler runs this at
post_output: it parses the structured candidates the agent emitted and writes each through
``GovernedMemoryStore.awrite`` — so the deterministic reconcile (new/reinforce/update) and, on a
changed value, the reconcile decision skill (refine/contradict → quarantine) all fire on a live run.
Parsing is deterministic (no second LLM); the reconcile judge stays the only model call.

Contract: the agent emits a JSON object with a ``memories`` array of ``{subject, attribute, value,
type?, confidence?, source?}``. Anything unparseable is a no-op (the agent simply proposed nothing),
never an error — a memory writer must not crash the run.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from swarmkit_runtime.governed_memory._models import MemoryCandidate

_VALID_TYPES = {"semantic", "profile", "procedural", "episodic", "working"}


def parse_candidates(agent_output: str) -> list[MemoryCandidate]:
    """Extract memory candidates from an agent's output. Tolerant: returns ``[]`` for any output
    that isn't a JSON object with a ``memories`` list, or whose entries lack subject/attribute/
    value."""
    data = _extract_json_object(agent_output)
    if data is None:
        return []
    raw = data.get("memories")
    if not isinstance(raw, list):
        return []
    candidates: list[MemoryCandidate] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        subject = str(entry.get("subject", "")).strip()
        attribute = str(entry.get("attribute", "")).strip()
        value = str(entry.get("value", "")).strip()
        if not subject or not attribute or not value:
            continue
        mtype = entry.get("type", "semantic")
        if mtype not in _VALID_TYPES:
            mtype = "semantic"
        confidence = entry.get("confidence", 1.0)
        try:
            conf = min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            conf = 1.0
        candidates.append(
            MemoryCandidate(
                subject=subject,
                attribute=attribute,
                value=value,
                type=mtype,
                confidence=conf,
                source=str(entry["source"]) if entry.get("source") else None,
            )
        )
    return candidates


async def governed_memory_post_output(
    *, agent_id: str, agent_output: str, store: Any
) -> dict[str, Any]:
    """Write every candidate the agent proposed through the governed write path. Returns a summary
    ``{written, by_op}`` (op counts). A no-candidate output writes nothing."""
    candidates = parse_candidates(agent_output)
    by_op: dict[str, int] = {}
    for candidate in candidates:
        # Record who inserted the fact: the agent that proposed it, unless it named its own source.
        stamped = candidate if candidate.source else replace(candidate, source=agent_id)
        outcome = await store.awrite(stamped)
        by_op[outcome.op] = by_op.get(outcome.op, 0) + 1
    return {"agent_id": agent_id, "written": len(candidates), "by_op": by_op}


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort parse of the first ``{...}`` JSON object in ``text`` (tolerates prose/fences)."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
