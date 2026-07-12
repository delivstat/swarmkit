"""HTTP review-queue endpoints — the shared surface for resolving harness gates.

The CLI (`swarmkit review …`), the serve web UI, and the fleet UI all resolve the same §6.2
permission and §6.3 input gates through this one API over the same on-disk ``ReviewQueue`` — so a
harness approval behaves identically whichever front-end an operator uses. Read + human-decision
only; the queue is append-only from the agent's perspective (invariant #4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from swarmkit_runtime.review import FileReviewQueue, ReviewItem


class AnswerRequest(BaseModel):
    answer: str


def _item_to_dict(item: ReviewItem) -> dict[str, Any]:
    """Serialize a review item for a front-end, surfacing the harness-gate fields (capability for a
    §6.2 permission, question/options for a §6.3 input) so a UI can render + resolve it."""
    kind = (
        "permission"
        if item.skill_id == "harness-approval"
        else "input"
        if item.skill_id == "harness-input"
        else "other"
    )
    return {
        "id": item.id,
        "kind": kind,
        "agent_id": item.agent_id,
        "topology_id": item.topology_id,
        "skill_id": item.skill_id,
        "reason": item.reason,
        "status": item.status,
        "answer": item.answer,
        "capability": item.output.get("capability", ""),
        "question": item.output.get("question", ""),
        "options": item.output.get("options", []),
        "free_text_allowed": item.output.get("free_text_allowed", True),
        "timestamp": item.timestamp.isoformat(),
    }


def _register_review_routes(app: FastAPI, workspace_path: Path) -> None:
    """GET /review[/all], GET /review/{id}, POST /review/{id}/(approve|reject|answer)."""

    def _queue() -> FileReviewQueue:
        return FileReviewQueue(workspace_path)

    def _find(queue: FileReviewQueue, item_id: str) -> ReviewItem:
        item = queue.get(item_id)
        if item is None:  # convenience: accept an id prefix, like the CLI
            matches = [i for i in queue.list_all() if i.id.startswith(item_id)]
            item = matches[0] if matches else None
        if item is None:
            raise HTTPException(status_code=404, detail=f"review item {item_id!r} not found")
        return item

    @app.get("/review")
    async def list_pending() -> list[dict[str, Any]]:
        return [_item_to_dict(i) for i in _queue().list_pending()]

    @app.get("/review/all")
    async def list_all() -> list[dict[str, Any]]:
        return [_item_to_dict(i) for i in _queue().list_all()]

    @app.get("/review/{item_id}")
    async def get_item(item_id: str) -> dict[str, Any]:
        return _item_to_dict(_find(_queue(), item_id))

    @app.post("/review/{item_id}/approve")
    async def approve(item_id: str) -> dict[str, Any]:
        queue = _queue()
        item = _find(queue, item_id)
        queue.resolve(item.id, "approved")
        return _item_to_dict(_find(queue, item.id))

    @app.post("/review/{item_id}/reject")
    async def reject(item_id: str) -> dict[str, Any]:
        queue = _queue()
        item = _find(queue, item_id)
        queue.resolve(item.id, "rejected")
        return _item_to_dict(_find(queue, item.id))

    @app.post("/review/{item_id}/answer")
    async def answer(item_id: str, body: AnswerRequest) -> dict[str, Any]:
        queue = _queue()
        item = _find(queue, item_id)
        # a bare integer selects an option index; else the text is used verbatim
        resolved = body.answer
        options = item.output.get("options") or []
        if body.answer.isdigit() and 0 <= int(body.answer) < len(options):
            resolved = str(options[int(body.answer)])
        queue.answer_input(item.id, resolved)
        return _item_to_dict(_find(queue, item.id))
