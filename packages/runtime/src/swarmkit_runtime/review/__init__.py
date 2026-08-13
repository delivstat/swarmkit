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
    status: Literal["pending", "approved", "rejected", "changes-requested"] = "pending"
    # For §6.3 input requests: the operator's textual answer (which option / free text). Empty for a
    # plain approve/reject gate. NOT the resolver identity — that is `resolved_by`; this field used
    # to carry both, and its meaning depended on the item kind.
    answer: str = ""
    #: The authenticated resolver. Multi-party quorum is counted against this.
    resolved_by: str = ""
    #: What the human said when deciding. Relayed to the agent, recorded on the audit.
    comment: str = ""
    #: The artifact this decision is ABOUT, and which round of the gate it belongs to. A gate
    #: re-opens on a rework loop against a NEW artifact, and a decision made against an earlier one
    #: is retained + rendered but does not count toward quorum
    #: (design/details/human-decision-comments.md).
    artifact_ref: str = ""
    round: int = 0


class ReviewQueue(Protocol):
    """Protocol for review queue implementations."""

    def submit(self, item: ReviewItem) -> None: ...
    def list_pending(self) -> list[ReviewItem]: ...
    #: Every item, resolved included. On the Protocol because a reader that has to reason about a
    #: gate needs its RESOLVED items too — pending-only cannot answer "did this gate pass".
    def list_all(self) -> list[ReviewItem]: ...
    def get(self, item_id: str) -> ReviewItem | None: ...
    def resolve(
        self, item_id: str, status: Literal["approved", "rejected"], comment: str = ""
    ) -> bool: ...
    def answer_input(self, item_id: str, answer: str, comment: str = "") -> bool: ...


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

    def _write(self, item_id: str, patch: dict[str, Any]) -> bool:
        path = self._dir / f"{item_id}.json"
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(patch)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _record_approval_wait(data)
        return True

    def resolve(
        self, item_id: str, status: Literal["approved", "rejected"], comment: str = ""
    ) -> bool:
        return self._write(item_id, {"status": status, "comment": comment})

    def answer_input(self, item_id: str, answer: str, comment: str = "") -> bool:
        """Resolve a §6.3 input request with the operator's textual answer (approves + records)."""
        return self._write(item_id, {"status": "approved", "answer": answer, "comment": comment})

    def record_resolution(
        self,
        item_id: str,
        status: Literal["approved", "rejected", "changes-requested"],
        resolved_by: str,
        *,
        comment: str = "",
    ) -> bool:
        """Resolve a role-task, recording the outcome, the resolver identity and their comment.

        The identity is load-bearing: multi-party quorum is counted against it, and a reject must
        carry it too so the engine can verify the rejecter was an eligible member of the role. It
        is written to `resolved_by`; `answer` is also set for one release so a reader that has not
        been upgraded (an older UI, a fleet panel) keeps working.
        """
        return self._write(
            item_id,
            {
                "status": status,
                "resolved_by": resolved_by,
                # Transitional: drop once every reader uses `resolved_by`.
                "answer": resolved_by,
                "comment": comment,
            },
        )


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
        answer=data.get("answer", ""),
        # Backward compatible: before human-decision-comments.md, a multi-party role-task crammed
        # the resolver identity into `answer`. An item written by that shape has no `resolved_by`,
        # so fall back to `answer` — a gate already in flight keeps counting toward quorum across
        # the upgrade instead of losing its approvals.
        resolved_by=data.get("resolved_by")
        or (data.get("answer", "") if data.get("skill_id") == "multi-party-approval" else ""),
        comment=data.get("comment", ""),
        artifact_ref=data.get("artifact_ref", ""),
        round=int(data.get("round", 0)),
    )


__all__ = [
    "FileReviewQueue",
    "ReviewItem",
    "ReviewQueue",
    "create_review_item",
]
