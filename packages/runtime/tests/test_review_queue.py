"""Tests for the review queue (M4).

See ``design/details/decision-skills.md`` §Review queue primitive.
"""

from __future__ import annotations

from pathlib import Path

from swael_runtime.review import FileReviewQueue, create_review_item


def test_submit_and_list_pending(tmp_path: Path) -> None:
    queue = FileReviewQueue(tmp_path)
    item = create_review_item(
        topology_id="review",
        agent_id="worker-1",
        skill_id="code-quality-review",
        output={"verdict": "pass", "confidence": 0.3},
        verdict={"verdict": "pass", "confidence": 0.3},
        reason="Low confidence",
    )
    queue.submit(item)

    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0].id == item.id
    assert pending[0].status == "pending"
    assert pending[0].reason == "Low confidence"


def test_resolve_approve(tmp_path: Path) -> None:
    queue = FileReviewQueue(tmp_path)
    item = create_review_item(
        topology_id="t",
        agent_id="a",
        skill_id="s",
        output={},
        verdict={},
        reason="test",
    )
    queue.submit(item)

    assert queue.resolve(item.id, "approved")

    pending = queue.list_pending()
    assert len(pending) == 0

    all_items = queue.list_all()
    assert len(all_items) == 1
    assert all_items[0].status == "approved"


def test_resolve_reject(tmp_path: Path) -> None:
    queue = FileReviewQueue(tmp_path)
    item = create_review_item(
        topology_id="t",
        agent_id="a",
        skill_id="s",
        output={},
        verdict={},
        reason="test",
    )
    queue.submit(item)

    assert queue.resolve(item.id, "rejected")

    all_items = queue.list_all()
    assert all_items[0].status == "rejected"


def test_resolve_nonexistent_returns_false(tmp_path: Path) -> None:
    queue = FileReviewQueue(tmp_path)
    assert queue.resolve("nonexistent", "approved") is False


def test_multiple_items(tmp_path: Path) -> None:
    queue = FileReviewQueue(tmp_path)
    for i in range(3):
        queue.submit(
            create_review_item(
                topology_id="t",
                agent_id=f"a-{i}",
                skill_id="s",
                output={},
                verdict={},
                reason=f"reason-{i}",
            )
        )

    assert len(queue.list_pending()) == 3

    first = queue.list_pending()[0]
    queue.resolve(first.id, "approved")

    assert len(queue.list_pending()) == 2
