"""A reviewer's "changes requested" comment must reach the re-run.

Reported against 1.133.0. `_rework()` received the event `data` — which carries the comment as
`detail` — and never read it. The stage re-ran with no knowledge of why, reproduced substantially
the same output, and the reviewer had no way to tell their feedback had had no effect. The
docstring claimed the opposite of what the code did.

The `add("resumed", ...)` call also overwrote `detail` with a fixed string, so the comment was not
even preserved in the timeline for a human to read afterwards.

What made it hard to see: comments DO reach the agent by a second route. `/review/{item}/resolve`
mints a resolved review item and `decisions_for_gate` renders it. That path works — so the feature
is real, and it fails only when the reviewer is not an authenticated identity. Under `auth: none`
that endpoint 403s (`approvals:resolve` is reserved for human identity), callers fall back to
enqueuing the controller event, and the comment travels only in `data["detail"]`. Configuration-
dependent silent data loss: the same UI action either delivered the comment or discarded it,
with nothing different for the reviewer to see.

Observed on WMS-1: a reviewer's domain correction — that cartons are loose inventory moved by the
pick process, and `getTaskList` identifies the tags and quantities moved for a shipment — was
recorded on the ticket and never reached the design agent.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.orchestration import StageOutcome
from swarmkit_runtime.orchestration._saga import SagaState
from swarmkit_runtime.orchestration._saga_store import _dict_to_tl, _tl_to_dict
from swarmkit_runtime.orchestration.reference._controller import ReferenceController
from swarmkit_runtime.server._pipeline_stage import _decisions_block, _saga_decisions

COMMENT = (
    "Cartons are loose inventory moved by the pick process; use getTaskList to identify the tags "
    "and quantities moved for a shipment."
)


class _Store:
    """Just enough saga store for the controller."""

    def __init__(self, saga: SagaState | None) -> None:
        self.saga = saga
        self.saved: list[SagaState] = []

    def get(self, _cid: str) -> SagaState | None:
        return self.saga

    def save(self, saga: SagaState) -> None:
        self.saved.append(saga)


def _parked_saga() -> SagaState:
    saga = SagaState(correlation_id="WMS-1", graph_id="wms-support", status="parked")
    saga.pending_gate_stage = "design"
    saga.artifacts["design"] = "WMS-1::design"
    return saga


async def _rework(saga: SagaState, data: dict[str, Any]) -> _Store:
    store = _Store(saga)
    controller = ReferenceController(
        run_stage=_noop_run_stage,
        store=store,  # type: ignore[arg-type]
        graphs={"wms-support": {"stages": [{"id": "design", "topology": "t"}]}},
    )
    await controller._rework("WMS-1", data)
    return store


async def _noop_run_stage(_cid: str, _stage: Any) -> Any:
    return StageOutcome(status="completed", detail="")


# ---- the comment survives the rework -------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_comment_is_kept_not_dropped() -> None:
    """The bug itself: `data["detail"]` was never read."""
    saga = _parked_saga()
    await _rework(saga, {"kind": "rework", "detail": COMMENT})

    carried = [e for e in saga.timeline if (e.meta or {}).get("comment")]
    assert carried, "the reviewer's comment was dropped — the re-run has nothing to act on"
    assert carried[0].meta["comment"] == COMMENT


@pytest.mark.asyncio
async def test_the_comment_is_readable_in_the_timeline() -> None:
    """The other half: the fixed string overwrote `detail`, so no later human could read the
    reason either. A saga timeline that says only "changes requested" is not a record."""
    saga = _parked_saga()
    await _rework(saga, {"kind": "rework", "detail": COMMENT})

    resumed = [e for e in saga.timeline if e.kind == "resumed"]
    assert resumed
    assert COMMENT in resumed[-1].detail


@pytest.mark.asyncio
async def test_the_comment_is_stamped_with_round_and_artifact() -> None:
    """Stamped so the agent can tell a note about the revision it just wrote from one about the
    draft two rounds ago — handing "add backoff" unlabelled to the revision that added backoff
    would have the agent undo its own fix."""
    saga = _parked_saga()
    await _rework(saga, {"kind": "rework", "detail": COMMENT})

    meta = next(e.meta for e in saga.timeline if (e.meta or {}).get("comment"))
    assert meta["round"] == 1, "first rework is round 1"
    assert meta["artifact_ref"] == "WMS-1::design", "stamped with what it was written against"


@pytest.mark.asyncio
async def test_a_rework_without_a_comment_still_works() -> None:
    """A rework with no reason is legitimate. It must not crash, and must not invent a comment."""
    saga = _parked_saga()
    await _rework(saga, {"kind": "rework"})

    # It unparks and drives on (the stub stage completes immediately, so the saga finishes).
    assert saga.status != "parked"
    assert saga.pending_gate_stage is None
    assert not [e for e in saga.timeline if (e.meta or {}).get("comment")]


# ---- and reaches the agent -----------------------------------------------------------------------


def test_the_comment_reaches_the_stage_input(tmp_path: Any) -> None:
    """End of the chain: what the re-running agent actually reads."""
    saga = _parked_saga()
    saga.add(
        "resumed",
        stage_id="design",
        detail=f"changes requested — re-running the stage: {COMMENT}",
        meta={"comment": COMMENT, "round": 1, "artifact_ref": "WMS-1::design"},
    )

    block = _decisions_block(tmp_path, "WMS-1", "design", saga)
    assert COMMENT in block, "the agent must see the reviewer's reason"
    assert "human-decisions" in block, "delimited as a decision, not spliced into instructions"


def test_the_override_is_labelled_as_one(tmp_path: Any) -> None:
    """No authenticated reviewer stands behind this decision, and the block must not imply one."""
    saga = _parked_saga()
    saga.add("resumed", stage_id="design", detail="x", meta={"comment": COMMENT, "round": 1})

    assert "operator-override" in _decisions_block(tmp_path, "WMS-1", "design", saga)


def test_comments_for_another_stage_are_not_leaked(tmp_path: Any) -> None:
    """A note about `triage` is not feedback on `design`."""
    saga = _parked_saga()
    saga.add("resumed", stage_id="triage", detail="x", meta={"comment": "about triage", "round": 1})

    assert _decisions_block(tmp_path, "WMS-1", "design", saga) == ""


def test_no_decisions_still_renders_nothing(tmp_path: Any) -> None:
    """A first attempt must not carry an empty decisions block — the agent would read a header
    announcing human feedback that is not there."""
    assert _decisions_block(tmp_path, "WMS-1", "design", _parked_saga()) == ""
    assert _decisions_block(tmp_path, "WMS-1", "design", None) == ""


def test_both_routes_converge_on_one_mechanism() -> None:
    """The point of the fix. Whether the decision came through the review store or the break-glass
    event path, it is the same `HumanDecision` shape rendered by the same function — so delivery no
    longer depends on which auth provider serve is running."""
    saga = _parked_saga()
    saga.add("resumed", stage_id="design", detail="x", meta={"comment": COMMENT, "round": 2})

    decisions = _saga_decisions(saga, "design")
    assert len(decisions) == 1
    assert decisions[0].outcome == "changes-requested"
    assert decisions[0].comment == COMMENT
    assert decisions[0].round == 2


# ---- persistence ---------------------------------------------------------------------------------


def test_meta_survives_a_round_trip_through_the_store() -> None:
    """The comment has to outlive a restart: the controller and the re-run are different processes.
    `meta` rides in the timeline's existing JSON column, so this needs no schema migration — there
    is no migration facility, and a new column would break existing deployments on next insert."""
    saga = _parked_saga()
    saga.add("resumed", stage_id="design", detail="d", meta={"comment": COMMENT, "round": 1})

    restored = _dict_to_tl(_tl_to_dict(saga.timeline[-1]))
    assert restored.meta["comment"] == COMMENT


def test_an_entry_without_meta_still_loads() -> None:
    """Rows written by an older build have no `meta` key at all. They must load, not explode."""
    legacy = {"seq": 0, "at": "2026-08-03T00:00:00Z", "stage_id": "design", "kind": "resumed"}
    assert _dict_to_tl(legacy).meta == {}


def test_empty_meta_is_not_written() -> None:
    """Keeps existing rows byte-identical and keeps the timeline readable."""
    saga = _parked_saga()
    saga.add("started", stage_id="design")
    assert "meta" not in _tl_to_dict(saga.timeline[-1])
