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
    def get(self, item_id: str) -> ReviewItem | None: ...
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

    def get(self, item_id: str) -> ReviewItem | None:
        path = self._dir / f"{item_id}.json"
        if not path.exists():
            return None
        return _from_dict(json.loads(path.read_text(encoding="utf-8")))

    def resolve(self, item_id: str, status: Literal["approved", "rejected"]) -> bool:
        path = self._dir / f"{item_id}.json"
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = status
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _record_approval_wait(data)
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


def _record_approval_wait(data: dict[str, Any]) -> None:
    """Emit the human-approval wait time (design: runtime/otel-metrics-export). Best-effort — a
    telemetry hiccup must never fail resolving a review. No-op when telemetry is disabled."""
    try:
        from swarmkit_runtime.telemetry import record_approval_wait  # noqa: PLC0415

        created = datetime.fromisoformat(data["timestamp"])
        wait_ms = int((datetime.now(tz=UTC) - created).total_seconds() * 1000)
        record_approval_wait(scope=str(data.get("skill_id") or "review"), wait_ms=max(0, wait_ms))
    except Exception:
        pass


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
