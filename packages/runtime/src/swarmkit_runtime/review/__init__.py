"""Review queue — HITL escalation for low-confidence or failed verdicts.

See ``design/details/decision-skills.md`` §Review queue primitive.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ReviewItem:
    """A single item in the review queue."""

    id: str
    topology_id: str
    agent_id: str
    skill_id: str
    output: dict[str, Any]
    verdict: dict[str, Any]
    reason: str
    timestamp: datetime
    status: Literal["pending", "approved", "rejected"] = "pending"


class ReviewQueue(Protocol):
    """Protocol for review queue implementations."""

    def submit(self, item: ReviewItem) -> None: ...
    def list_pending(self) -> list[ReviewItem]: ...
    def resolve(self, item_id: str, status: Literal["approved", "rejected"]) -> bool: ...


class FileReviewQueue:
    """File-backed review queue under ``.swarmkit/reviews/``."""

    def __init__(self, base_dir: Path) -> None:
        self._dir = base_dir / ".swarmkit" / "reviews"
        self._dir.mkdir(parents=True, exist_ok=True)

    def submit(self, item: ReviewItem) -> None:
        data = asdict(item)
        data["timestamp"] = item.timestamp.isoformat()
        path = self._dir / f"{item.id}.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_pending(self) -> list[ReviewItem]:
        items: list[ReviewItem] = []
        for path in sorted(self._dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("status") == "pending":
                items.append(_from_dict(data))
        return items

    def list_all(self) -> list[ReviewItem]:
        items: list[ReviewItem] = []
        for path in sorted(self._dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append(_from_dict(data))
        return items

    def resolve(self, item_id: str, status: Literal["approved", "rejected"]) -> bool:
        path = self._dir / f"{item_id}.json"
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = status
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True


def create_review_item(
    *,
    topology_id: str,
    agent_id: str,
    skill_id: str,
    output: dict[str, Any],
    verdict: dict[str, Any],
    reason: str,
) -> ReviewItem:
    """Factory for creating review items with auto-generated id + timestamp."""
    return ReviewItem(
        id=str(uuid.uuid4()),
        topology_id=topology_id,
        agent_id=agent_id,
        skill_id=skill_id,
        output=output,
        verdict=verdict,
        reason=reason,
        timestamp=datetime.now(tz=UTC),
    )


def _from_dict(data: dict[str, Any]) -> ReviewItem:
    return ReviewItem(
        id=data["id"],
        topology_id=data["topology_id"],
        agent_id=data["agent_id"],
        skill_id=data["skill_id"],
        output=data["output"],
        verdict=data["verdict"],
        reason=data["reason"],
        timestamp=datetime.fromisoformat(data["timestamp"]),
        status=data.get("status", "pending"),
    )


__all__ = [
    "FileReviewQueue",
    "ReviewItem",
    "ReviewQueue",
    "create_review_item",
]
