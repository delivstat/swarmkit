"""JSON-serializable shapes for governed memory — the wire contract the CLI and serve share
(design/details/governed-memory.md; the CLI ⇄ serve parity rule, workspace-ui.md).

One serializer per value object so `swarmkit memory …` and the `/memory` endpoints emit identical
JSON; a front-end renders the same data whichever surface it came from.
"""

from __future__ import annotations

from typing import Any

from swarmkit_runtime.governed_memory._models import ChangeLogEntry, Memory, QuarantineItem


def memory_to_dict(m: Memory) -> dict[str, Any]:
    return {
        "key": m.key,
        "subject": m.subject,
        "attribute": m.attribute,
        "value": m.value,
        "type": m.type,
        "confidence": m.confidence,
        "valid_from": m.valid_from,
        "last_reinforced_at": m.last_reinforced_at,
        "reinforce_count": m.reinforce_count,
        "source": m.source,
        "status": m.status,
    }


def change_to_dict(e: ChangeLogEntry) -> dict[str, Any]:
    return {
        "id": e.id,
        "op": e.op,
        "before": e.before,
        "after": e.after,
        "reason": e.reason,
        "decided_by": e.decided_by,
        "timestamp": e.timestamp,
    }


def quarantine_to_dict(q: QuarantineItem) -> dict[str, Any]:
    return {
        "id": q.id,
        "memory_key": q.memory_key,
        "candidate": q.candidate,
        "current_value": q.current_value,
        "reasoning": q.reasoning,
        "status": q.status,
        "created_at": q.created_at,
        "resolved_at": q.resolved_at,
        "resolved_by": q.resolved_by,
    }
