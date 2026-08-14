"""A funnel's `approve` layer parks the run on a human, and resuming does not re-draft.

1.172.0 made the layer advisory — it recorded and passed — justified as *"human approval is the
stage-level `gate:`, which parks the saga durably"*. That is unavailable to a run with no saga,
which is every run an application sequences itself, and it was the weaker of the two branches
considered: the choice was framed as *block the coroutine for seven days* or *pass advisorily*, when
defer-and-resume already existed and beats both.

**The part that makes this more than "raise instead of return".** LangGraph checkpoints at
super-step boundaries, so a node that raised is RE-RUN on resume. A naive defer would re-draft the
artifact after approval — ~$2.40 on a design agent — and the human would have approved something
that no longer exists. So the gated node reads its gate before producing anything:

* approved → return the artifact off the gate, produce nothing;
* rejected → return the rejection with the resolver's comment, produce nothing;
* pending → defer again, which is the same code path as the first defer;
* no gate → produce.

`test_resuming_after_approval_does_not_call_the_model_again` is the assertion the whole design
exists for.

Two states stay advisory rather than parking, because parking would be worse than not gating: a
policy naming roles no RoleRegistry defines (nobody could satisfy it, so every run would wait
forever on an approver who does not exist), and a run with no run id (a gate is found again by its
run, so without one no approval could ever be located on re-entry).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime._run_scope import reset_current_run_id, set_current_run_id
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.model_providers import (
    CompletionResponse,
    ContentBlock,
    MockModelProvider,
    ProviderRegistry,
    Usage,
)
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._hitl import GateDeferredError
from swarmkit_runtime.review._multiparty import role_task_item_id

pytestmark = pytest.mark.asyncio

RUN = "run-1"
GATE = f"{RUN}:designer"
DRAFT = '{"summary": "the spec"}'

APPROVE: dict[str, Any] = {
    "rules": [{"scope": "design:approve", "roles": ["oms-lead"], "quorum": "all"}],
    "exclude_author": False,
}


def _workspace(tmp_path: Path, *, roles: bool = True) -> Path:
    root = tmp_path / "ws"
    for sub in ("topologies", "funnels", "roles"):
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
                    "description": "a funnel whose approve layer parks the run on a human",
                },
                "approve": APPROVE,
                "provenance": {"authored_by": "human", "version": "1.0.0"},
            }
        )
    )
    if roles:
        (root / "roles" / "leads.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "swarmkit/v1",
                    "kind": "RoleRegistry",
                    "metadata": {
                        "id": "leads",
                        "name": "Leads",
                        "description": "who may approve a design in this workspace",
                    },
                    "roles": [
                        {"id": "oms-lead", "members": ["alice"], "scopes": ["design:approve"]}
                    ],
                }
            )
        )
    (root / "topologies" / "design.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Topology",
                "metadata": {"name": "design", "version": "0.1.0"},
                "agents": {
                    "root": {
                        "id": "designer",
                        "role": "root",
                        "funnel": "spec-review",
                        "model": {"provider": "mock", "name": "mock"},
                    },
                },
            }
        )
    )
    return root


class _Counting(MockModelProvider):
    """Counts completions, so "did it re-draft?" is answerable rather than inferred."""

    def __init__(self) -> None:
        super().__init__()
        self.drafts = 0

    async def complete(self, request: Any) -> CompletionResponse:
        self.drafts += 1
        return CompletionResponse(
            content=(ContentBlock(type="text", text=DRAFT),),
            stop_reason="end_turn",
            usage=Usage(),
        )


def _runtime(root: Path, provider: _Counting) -> WorkspaceRuntime:
    from swarmkit_runtime.resolver import resolve_workspace  # noqa: PLC0415

    registry = ProviderRegistry()
    registry.register(provider)
    return WorkspaceRuntime(
        workspace=resolve_workspace(root),
        workspace_root=root,
        provider_registry=registry,
        governance=MockGovernanceProvider(allow_all=True),
        mcp_manager=None,
    )


async def _run(runtime: WorkspaceRuntime) -> Any:
    return await runtime.run("design", "draft the spec", thread_id=RUN)


def _approve(root: Path, who: str = "alice") -> None:
    FileReviewQueue(root).record_resolution(
        role_task_item_id(GATE, 0, "oms-lead", 0), "approved", who
    )


def _reject(root: Path, comment: str) -> None:
    FileReviewQueue(root).record_resolution(
        role_task_item_id(GATE, 0, "oms-lead", 0), "rejected", "alice", comment=comment
    )


# ---- the run parks --------------------------------------------------------------------------


async def test_a_gated_run_defers_instead_of_passing(tmp_path: Path) -> None:
    """The reversal of 1.172.0: the layer no longer records and passes."""
    root = _workspace(tmp_path)

    with pytest.raises(GateDeferredError) as exc:
        await _run(_runtime(root, _Counting()))

    assert exc.value.gate_id == GATE


async def test_role_tasks_are_opened_for_a_human(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    with pytest.raises(GateDeferredError):
        await _run(_runtime(root, _Counting()))

    pending = FileReviewQueue(root).list_pending()
    assert [(i.output or {}).get("role") for i in pending] == ["oms-lead"]


async def test_the_artifact_is_on_the_gate_for_the_reviewer(tmp_path: Path) -> None:
    """A reviewer must see what they are approving, and the resumed node must return exactly it."""
    root = _workspace(tmp_path)

    with pytest.raises(GateDeferredError):
        await _run(_runtime(root, _Counting()))

    item = FileReviewQueue(root).list_pending()[0]
    assert (item.output or {}).get("artifact") == DRAFT


# ---- and resuming does not re-draft -------------------------------------------------------------


async def test_resuming_after_approval_does_not_call_the_model_again(tmp_path: Path) -> None:
    """The assertion the whole design exists for.

    LangGraph re-runs the node on resume. Without re-entrancy this would draft again — paying twice
    and, worse, returning an artifact the approver never saw.
    """
    root = _workspace(tmp_path)
    provider = _Counting()
    runtime = _runtime(root, provider)

    with pytest.raises(GateDeferredError):
        await _run(runtime)
    drafted = provider.drafts
    assert drafted >= 1

    _approve(root)
    result = await _run(_runtime(root, provider))

    assert provider.drafts == drafted, "resuming after approval must not re-draft"
    assert DRAFT in result.output or DRAFT in str(result.agent_results)


async def test_the_approved_artifact_is_the_one_the_human_saw(tmp_path: Path) -> None:
    """Read back off the gate, not re-produced — even if the model would now answer differently."""
    root = _workspace(tmp_path)
    provider = _Counting()

    with pytest.raises(GateDeferredError):
        await _run(_runtime(root, provider))
    _approve(root)

    class _Different(_Counting):
        async def complete(self, request: Any) -> CompletionResponse:
            self.drafts += 1
            return CompletionResponse(
                content=(ContentBlock(type="text", text='{"summary": "SOMETHING ELSE"}'),),
                stop_reason="end_turn",
                usage=Usage(),
            )

    result = await _run(_runtime(root, _Different()))

    assert "SOMETHING ELSE" not in str(result.agent_results)


async def test_resuming_while_still_pending_defers_again(tmp_path: Path) -> None:
    """The same code path as the first defer, not a special case — and still no re-draft."""
    root = _workspace(tmp_path)
    provider = _Counting()

    with pytest.raises(GateDeferredError):
        await _run(_runtime(root, provider))
    drafted = provider.drafts

    with pytest.raises(GateDeferredError):
        await _run(_runtime(root, provider))

    assert provider.drafts == drafted, "a pending gate must not re-draft either"


async def test_a_rejection_carries_the_comment_back(tmp_path: Path) -> None:
    """A human rejection is a critique, and the run must say so rather than silently passing."""
    root = _workspace(tmp_path)
    provider = _Counting()

    with pytest.raises(GateDeferredError):
        await _run(_runtime(root, provider))
    _reject(root, "the impact section is missing")

    result = await _run(_runtime(root, provider))

    assert "GATE REJECTED" in str(result.agent_results)
    assert "impact section" in str(result.agent_results)


# ---- the record survives the parking ------------------------------------------------------------


async def test_a_parked_run_still_writes_its_audit_events(tmp_path: Path) -> None:
    """The run raised, so the completing path never ran. Without finalising on the parking path
    too, a deferred run left no trace and no audit of work it had already paid for."""
    root = _workspace(tmp_path)
    runtime = _runtime(root, _Counting())

    with pytest.raises(GateDeferredError):
        await _run(runtime)

    events = [e async for e in runtime.audit_provider.query(run_id=RUN, limit=100)]
    assert any(e.event_type == "funnel.gate_opened" for e in events)


# ---- what stays advisory, and why ---------------------------------------------------------------


async def test_a_policy_naming_unknown_roles_stays_advisory(tmp_path: Path) -> None:
    """Nobody could satisfy it, so parking would wait forever on an approver who does not exist —
    the "stall nobody can release", which is worse than not gating."""
    root = _workspace(tmp_path, roles=False)

    result = await _run(_runtime(root, _Counting()))

    assert result.output or result.agent_results


async def test_a_run_without_a_run_id_stays_advisory(tmp_path: Path) -> None:
    """A gate is identified by its run; with no run id there is nothing stable to key on, so no
    approval could be found on re-entry and the caller would be stranded."""
    from swarmkit_runtime.langgraph_compiler._compiler import _enforces_gate  # noqa: PLC0415

    token = set_current_run_id(None)
    try:
        assert _enforces_gate("spec-review") is False
    finally:
        reset_current_run_id(token)

    token = set_current_run_id("run-1")
    try:
        assert _enforces_gate("spec-review") is True
    finally:
        reset_current_run_id(token)


# ---- the gate id is unique per run ---------------------------------------------------------------


async def test_two_runs_get_two_gates(tmp_path: Path) -> None:
    """It used to be `{topology}:{agent}`, so every run of a topology shared one gate: two tickets
    in flight had quorum counted across both, and approving one released the other."""
    root = _workspace(tmp_path)
    provider = _Counting()

    with pytest.raises(GateDeferredError):
        await _runtime(root, provider).run("design", "a", thread_id="run-a")
    with pytest.raises(GateDeferredError):
        await _runtime(root, provider).run("design", "b", thread_id="run-b")

    gates = {(i.output or {}).get("gate_id") for i in FileReviewQueue(root).list_all()}
    assert gates == {"run-a:designer", "run-b:designer"}


async def test_approving_one_run_does_not_release_another(tmp_path: Path) -> None:
    """The consequence that makes the collision a governance bug rather than an annoyance."""
    root = _workspace(tmp_path)
    provider = _Counting()

    with pytest.raises(GateDeferredError):
        await _runtime(root, provider).run("design", "a", thread_id="run-a")
    with pytest.raises(GateDeferredError):
        await _runtime(root, provider).run("design", "b", thread_id="run-b")

    FileReviewQueue(root).record_resolution(
        role_task_item_id("run-a:designer", 0, "oms-lead", 0), "approved", "alice"
    )

    with pytest.raises(GateDeferredError):
        await _runtime(root, provider).run("design", "b", thread_id="run-b")


# ---- serve records a pause, not a failure --------------------------------------------------------


def test_serve_treats_a_deferral_as_deferred_not_failed() -> None:
    """Serve caught only generic exceptions, so a run parked on a human was recorded as a FAILED
    job — the CLI has handled deferral since HITL landed and serve never learned to."""
    from pathlib import Path as _Path  # noqa: PLC0415

    src = (
        _Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_jobs.py"
    ).read_text()

    assert "except HITLDeferredError" in src
    assert 'job.status = "deferred"' in src
    assert src.index("except HITLDeferredError") < src.index("except Exception as exc:")


def test_the_deferred_status_is_a_declared_job_state() -> None:
    """A status the type does not admit is a status no reader can rely on."""
    from swarmkit_runtime.server._jobs import Job  # noqa: PLC0415

    assert "deferred" in str(Job.__annotations__["status"])


def test_json_round_trip_of_the_gate_artifact(tmp_path: Path) -> None:
    """The artifact rides on the review item, which is JSON on disk."""
    root = _workspace(tmp_path)
    (root / ".swarmkit" / "reviews").mkdir(parents=True, exist_ok=True)
    queue = FileReviewQueue(root)
    from swarmkit_runtime.governance._approval import ApprovalPolicy  # noqa: PLC0415
    from swarmkit_runtime.review._multiparty import open_gate  # noqa: PLC0415

    open_gate(
        queue,
        gate_id=GATE,
        topology_id="design",
        agent_id="designer",
        policy=ApprovalPolicy.from_dict(APPROVE),
        funnel_id="spec-review",
        artifact_ref="ref",
        artifact=DRAFT,
    )

    item = queue.list_all()[0]
    assert json.loads(str((item.output or {}).get("artifact"))) == {"summary": "the spec"}
