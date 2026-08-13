"""A caller can ask whether a gate is resolved, with the approval policy applied.

`GET /review?gate_id=…` returns the individual role-tasks. Turning those into a decision means
applying quorum, distinct-approver counts and `exclude_author` — logic that lives in
`collect_resolutions` and `evaluate`, and that a caller **cannot** derive for itself, because the
policy lives in a funnel the caller does not read.

Without this, an application sequencing its own runs either reimplements the approval policy or
approximates it. An approximation of an approval policy is a governance failure with a friendly
name, which is why the interesting tests here are the ones where a naive "any approval means
approved" reading would be wrong: partial quorum, the author's own approval, and a decision cast
against a superseded artifact.

**The policy is resolved from the items, not from the gate id.** `open_gate` stamps `funnel_id` on
every role-task for exactly this purpose. Two gate-id shapes exist today — `{topology}:{agent}`
in-node and `{correlation}:{agent}` for a stage — so a resolver that parsed the id would be wrong
for one of them, and wrong again when the shapes are unified.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime.gate_state import (
    PolicyUnresolvedError,
    UnknownGateError,
    compute_gate_state,
    gate_ids,
)
from swarmkit_runtime.governance._approval import ApprovalPolicy
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import open_gate, role_task_item_id

GATE = "wms-design:designer"

APPROVE: dict[str, Any] = {
    "rules": [{"scope": "design:approve", "roles": ["oms-lead", "web-lead"], "quorum": "all"}],
    "exclude_author": True,
}


def _workspace(tmp_path: Path, approve: dict[str, Any] | None = None) -> Path:
    root = tmp_path / "ws"
    for sub in ("funnels", "roles"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "w", "name": "w"},
            }
        )
    )
    (root / "funnels" / "spec-review.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Funnel",
                "metadata": {
                    "id": "spec-review",
                    "name": "Spec Review",
                    "description": "a funnel whose approve layer this test evaluates",
                },
                "approve": approve or APPROVE,
                "provenance": {"authored_by": "human", "version": "1.0.0"},
            }
        )
    )
    (root / "roles" / "leads.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "RoleRegistry",
                "metadata": {
                    "id": "leads",
                    "name": "Leads",
                    "description": "the approvers this workspace's gates are evaluated against",
                },
                "roles": [
                    {"id": "oms-lead", "members": ["alice"], "scopes": ["design:approve"]},
                    {"id": "web-lead", "members": ["bob"], "scopes": ["design:approve"]},
                ],
            }
        )
    )
    return root


def _open(root: Path, *, artifact: str = "WMS-35/run-1/output") -> FileReviewQueue:
    queue = FileReviewQueue(root)
    open_gate(
        queue,
        gate_id=GATE,
        topology_id="wms-design",
        agent_id="designer",
        policy=ApprovalPolicy.from_dict(APPROVE),
        funnel_id="spec-review",
        artifact_ref=artifact,
    )
    return queue


def _state(root: Path, queue: FileReviewQueue, **kw: Any) -> Any:
    workspace = resolve_workspace(root)
    return compute_gate_state(queue, workspace.role_registry, workspace, GATE, **kw)


def _approve(queue: FileReviewQueue, role: str, who: str, round_no: int = 0) -> None:
    """Resolve a role-task the way the real approve path does — identity included.

    `record_resolution`, not `resolve`: quorum is counted against the resolver identity, so an
    approval without one is not an approval the engine can count.
    """
    queue.record_resolution(role_task_item_id(GATE, 0, role, round_no), "approved", who)


# ---- the readings a naive caller would get wrong ------------------------------------------------


def test_partial_quorum_is_pending_not_approved(tmp_path: Path) -> None:
    """One of two roles approved. "Something was approved" is not "the gate is approved"."""
    root = _workspace(tmp_path)
    queue = _open(root)
    _approve(queue, "oms-lead", "alice")

    state = _state(root, queue)

    assert state.status == "pending"
    assert state.resolved is False
    assert "web-lead (design:approve)" in state.outstanding


def test_full_quorum_is_approved(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    queue = _open(root)
    _approve(queue, "oms-lead", "alice")
    _approve(queue, "web-lead", "bob")

    state = _state(root, queue)

    assert state.status == "approved"
    assert state.resolved is True
    assert state.distinct_approvers == ("alice", "bob")


def test_a_rejection_is_terminal(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    queue = _open(root)
    _approve(queue, "oms-lead", "alice")
    queue.record_resolution(role_task_item_id(GATE, 0, "web-lead", 0), "rejected", "bob")

    state = _state(root, queue)

    assert state.status == "rejected"
    assert state.resolved is True


def test_the_authors_own_approval_does_not_count(tmp_path: Path) -> None:
    """`exclude_author` is segregation of duties. A caller counting approvals cannot see it."""
    root = _workspace(tmp_path)
    queue = _open(root)
    _approve(queue, "oms-lead", "alice")
    _approve(queue, "web-lead", "bob")

    state = _state(root, queue, author="alice")

    assert state.status == "pending", "alice authored it, so her approval must not count"


def test_an_unknown_author_is_flagged_not_ignored(tmp_path: Path) -> None:
    """The policy excludes the author and no author was supplied, so that rule was NOT applied.

    Saying so is the point: without the flag a caller reads `approved` for a gate whose real
    evaluation might discount an approval, and being wrong in the permissive direction is the worst
    kind of wrong for an approval.
    """
    root = _workspace(tmp_path)
    queue = _open(root)

    assert _state(root, queue).exclude_author_unapplied is True
    assert _state(root, queue, author="alice").exclude_author_unapplied is False


def test_a_policy_without_exclude_author_is_not_flagged(tmp_path: Path) -> None:
    approve: dict[str, Any] = {
        "rules": [{"scope": "design:approve", "roles": ["oms-lead"], "quorum": "all"}],
        "exclude_author": False,
    }
    root = _workspace(tmp_path, approve)
    queue = FileReviewQueue(root)
    open_gate(
        queue,
        gate_id=GATE,
        topology_id="wms-design",
        agent_id="designer",
        policy=ApprovalPolicy.from_dict(approve),
        funnel_id="spec-review",
        artifact_ref="a",
    )

    assert _state(root, queue).exclude_author_unapplied is False


def test_a_decision_on_a_superseded_artifact_does_not_count(tmp_path: Path) -> None:
    """A rework produced a new artifact; the earlier approval was about a different document.

    It stays visible and marked stale — retained, not counted (`collect_resolutions`).
    """
    root = _workspace(tmp_path)
    queue = _open(root, artifact="WMS-35/run-1/output")
    _approve(queue, "oms-lead", "alice")
    _approve(queue, "web-lead", "bob")
    assert _state(root, queue).status == "approved"

    # the agent re-ran and produced a new artifact
    open_gate(
        queue,
        gate_id=GATE,
        topology_id="wms-design",
        agent_id="designer",
        policy=ApprovalPolicy.from_dict(APPROVE),
        funnel_id="spec-review",
        artifact_ref="WMS-35/run-2/output",
    )

    state = _state(root, queue)

    assert state.status == "pending", "approvals cast on the previous artifact must not carry over"
    assert state.artifact_ref == "WMS-35/run-2/output"
    assert any(r.stale for r in state.resolutions), "the earlier decisions stay visible"


# ---- resolution is by funnel, not by parsing the id ---------------------------------------------


def test_a_stage_shaped_gate_id_resolves_too(tmp_path: Path) -> None:
    """`{correlation}:{agent}` carries no topology, so an id-parsing resolver would fail here."""
    root = _workspace(tmp_path)
    queue = FileReviewQueue(root)
    open_gate(
        queue,
        gate_id="WMS-27:design",
        topology_id="wms-design",
        agent_id="designer",
        policy=ApprovalPolicy.from_dict(APPROVE),
        funnel_id="spec-review",
        artifact_ref="a",
    )
    workspace = resolve_workspace(root)

    state = compute_gate_state(queue, workspace.role_registry, workspace, "WMS-27:design")

    assert state.status == "pending"
    assert state.funnel_id == "spec-review"


def test_an_unknown_gate_raises(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    with pytest.raises(UnknownGateError):
        _state(root, FileReviewQueue(root))


def test_an_unresolvable_policy_raises_rather_than_guessing(tmp_path: Path) -> None:
    """A gate whose policy cannot be read is not the same as a gate that is pending, and reporting
    one as the other is how an approval gets skipped."""
    root = _workspace(tmp_path)
    queue = FileReviewQueue(root)
    open_gate(
        queue,
        gate_id=GATE,
        topology_id="wms-design",
        agent_id="designer",
        policy=ApprovalPolicy.from_dict(APPROVE),
        funnel_id="not-in-this-workspace",
        artifact_ref="a",
    )
    workspace = resolve_workspace(root)

    with pytest.raises(PolicyUnresolvedError):
        compute_gate_state(queue, workspace.role_registry, workspace, GATE)


# ---- the shape a caller consumes ----------------------------------------------------------------


def test_the_state_serialises(tmp_path: Path) -> None:
    """`GET /gates/{gate_id}` returns this verbatim."""
    root = _workspace(tmp_path)
    queue = _open(root)
    _approve(queue, "oms-lead", "alice")

    payload = _state(root, queue).to_dict()

    assert payload["status"] == "pending"
    assert payload["resolved"] is False
    assert payload["funnel_id"] == "spec-review"
    assert payload["artifact_ref"] == "WMS-35/run-1/output"
    assert json.dumps(payload), "must be JSON-serialisable"


def test_gate_ids_lists_what_is_in_the_queue(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _open(root)

    assert gate_ids(FileReviewQueue(root)) == [GATE]
