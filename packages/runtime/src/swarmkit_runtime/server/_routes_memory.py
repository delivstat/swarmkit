"""HTTP governed-memory endpoints — the serve half of the ``swarmkit memory`` surface.

The CLI (`swarmkit memory …`) and these ``/memory`` endpoints resolve the same
``GovernedMemoryStore`` (via ``WorkspaceRuntime.governed_memory``) and emit the same JSON — CLI ⇄
serve parity (design/details/workspace-ui.md). Search + history are reads; the one write is
resolving a quarantined contradiction, the hard human gate (design §8). Memory itself is never
mutated here — a front-end can inspect and adjudicate, not overwrite.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from swarmkit_runtime.governed_memory import (
    change_to_dict,
    memory_to_dict,
    quarantine_to_dict,
)

from ._helpers import _get_runtime


class ResolveQuarantineRequest(BaseModel):
    """Curator decision on a quarantined contradiction: accept applies it, reject discards it."""

    resolved_by: str
    accept: bool


def _store(request: Request) -> Any:
    store = _get_runtime(request).governed_memory
    if store is None:
        raise HTTPException(status_code=404, detail="This workspace has no governed memory")
    return store


def _register_memory_routes(app: FastAPI) -> None:
    @app.get("/memory")
    def search_memory(
        request: Request, query: str = "", type: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        hits = _store(request).search(query, types=[type] if type else None, limit=limit)
        return {"memories": [memory_to_dict(m) for m in hits]}

    @app.get("/memory/item")
    def get_memory(
        request: Request, subject: str, attribute: str, history: bool = False
    ) -> dict[str, Any]:
        store = _store(request)
        current = store.get(subject, attribute)
        log = store.history(subject, attribute) if history else []
        return {
            "current": memory_to_dict(current) if current else None,
            "history": [change_to_dict(e) for e in log],
        }

    @app.get("/memory/quarantine")
    def list_quarantine(request: Request, status: str = "pending") -> dict[str, Any]:
        items = _store(request).list_quarantine(status=status)
        return {"quarantine": [quarantine_to_dict(q) for q in items]}

    @app.post("/memory/quarantine/{quarantine_id}/resolve")
    def resolve_quarantine(
        request: Request, quarantine_id: int, body: ResolveQuarantineRequest
    ) -> dict[str, Any]:
        outcome = _store(request).resolve_quarantine(
            quarantine_id, accept=body.accept, resolved_by=body.resolved_by
        )
        if body.accept and outcome is None:
            raise HTTPException(status_code=404, detail=f"No pending quarantine #{quarantine_id}")
        return {
            "resolved": True,
            "accepted": body.accept,
            "outcome": (
                {"op": outcome.op, "value": outcome.memory.value} if outcome is not None else None
            ),
        }
