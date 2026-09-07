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


class MemoryWrite(BaseModel):
    """A fact somebody asserts. `source` defaults to the authenticated caller — "who asserted
    this" is the question asked later, so it is never left null."""

    subject: str
    attribute: str
    value: str
    type: str = "semantic"
    confidence: float = 1.0
    source: str = ""


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

    @app.post("/memory")
    def add_memory(request: Request, body: MemoryWrite) -> dict[str, Any]:
        """Write a fact through the same governed path an agent writes through.

        Same fields, same reconcile and same response as `swarmkit memory add`, so an application
        owning its own sequencing records what a resolution established without shelling out — and
        the two surfaces cannot diverge the way the run surfaces once did.

        A `contradict` is **200 with `op: contradict`**, not an error: the request was valid and
        the store did exactly what it should. `changed` is what a caller branches on.
        """
        from swarmkit_runtime.governed_memory import MemoryCandidate  # noqa: PLC0415

        identity = getattr(request.state, "identity", None)
        source = body.source or getattr(identity, "client_id", "") or "api"
        outcome = _store(request).write(
            MemoryCandidate(
                subject=body.subject,
                attribute=body.attribute,
                value=body.value,
                type=body.type,  # type: ignore[arg-type]
                confidence=body.confidence,
                source=source,
            )
        )
        return {
            "op": outcome.op,
            "key": f"{body.subject}/{body.attribute}",
            "changed": bool(getattr(outcome, "changed", outcome.op != "contradict")),
        }

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
