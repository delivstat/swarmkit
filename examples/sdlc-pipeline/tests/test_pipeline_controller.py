"""Pipeline controller — saga sequencing, locking, failure-vs-wait, compensation (slice 5).

Covers the design test matrix in design/details/pipeline-controller.md: event dedup +
out-of-order + reconciliation-recovers-dropped; per-contract locking (serialise / disjoint /
all-or-none / no-deadlock); failure vs wait; cancellation + compensation; correlation; and the
defect loop. Most tests use a scripted ``run_stage`` seam; one wires the real ``StageRunner``
against a seeded review queue to prove the seam integrates.

The controller ships under ``examples/sdlc-pipeline`` (it is not a runtime feature and is not
installed), so we put that directory on ``sys.path`` to import it.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE = _REPO_ROOT / "examples" / "sdlc-pipeline"
_WS = _EXAMPLE / "workspace"
if str(_EXAMPLE) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE))

from controller import (  # type: ignore[import-not-found]  # noqa: E402
    InboundEvent,
    LockManager,
    PipelineController,
    Stage,
    StageGraph,
    StageRunOutcome,
    StageRunRequest,
    SurfaceNotice,
)

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------------------------


def _real_graph() -> Any:
    from swarmkit_runtime.resolver import resolve_workspace  # noqa: PLC0415

    ws = resolve_workspace(_WS)
    return StageGraph.from_spec(ws.stage_graphs["oms-pipeline"].spec)


def _linear_graph() -> Any:
    """A tiny 3-stage linear graph (no locks/gates) for routing + failure tests."""
    stages = [
        Stage("a", "topo-a", ("e.a",), "e.b", (), None, None, None),
        Stage("b", "topo-b", ("e.b",), "e.c", (), None, None, None),
        Stage("c", "topo-c", ("e.c",), None, (), None, None, None),
    ]
    return StageGraph("linear", stages, [])


class _Seam:
    """A scripted ``run_stage`` seam: records calls, parks gated stages, fails on a plan."""

    def __init__(
        self,
        *,
        gated: tuple[str, ...] = (),
        fail_plan: dict[str, int] | None = None,
        park_all: bool = False,
    ) -> None:
        self.calls: list[StageRunRequest] = []
        self._gated = set(gated)
        self._fail_plan = dict(fail_plan or {})
        self._park_all = park_all

    def kicked(self, topology: str) -> int:
        return sum(1 for c in self.calls if c.topology == topology)

    async def __call__(self, request: StageRunRequest) -> StageRunOutcome:
        self.calls.append(request)
        if request.is_compensation:
            return StageRunOutcome(status="completed")
        remaining = self._fail_plan.get(request.topology, 0)
        if remaining > 0:
            self._fail_plan[request.topology] = remaining - 1
            return StageRunOutcome(status="failed", detail="scripted failure")
        if self._park_all or request.topology in self._gated:
            return StageRunOutcome(status="parked")
        return StageRunOutcome(status="completed")


def _controller(graph: Any, seam: _Seam, **kwargs: Any) -> Any:
    return PipelineController(graph, seam, **kwargs)


async def _drive_to_build_wait(seam: _Seam, controller: Any, req: str) -> None:
    """Advance a requirement on the real graph to the external build.ready-in-qa wait."""
    await controller.handle_event(InboundEvent(req, "requirement.created", f"{req}-jira"))
    await controller.resolve_gate(req, approved=True)


# --------------------------------------------------------------------------------------------
# Event dedup + out-of-order + reconciliation
# --------------------------------------------------------------------------------------------


async def test_duplicate_event_is_a_noop() -> None:
    seam = _Seam()
    controller = _controller(_linear_graph(), seam)
    await controller.handle_event(InboundEvent("R1", "e.a", "src-1"))
    await controller.handle_event(InboundEvent("R1", "e.a", "src-1"))  # same key -> dedup
    assert seam.kicked("topo-a") == 1
    kinds = [e.kind for e in controller.timeline("R1")]
    assert "event.duplicate" in kinds


async def test_same_event_distinct_source_ids_do_not_double_advance() -> None:
    # Idempotency is keyed on source_event_id; the stage-level guard prevents a re-run when the
    # "same" logical event arrives twice with different source ids (webhook + reconciliation).
    seam = _Seam(park_all=True)  # stage stays in-flight so it is not a done saga
    controller = _controller(_linear_graph(), seam)
    await controller.handle_event(InboundEvent("R1", "e.a", "src-1"))
    await controller.handle_event(InboundEvent("R1", "e.a", "src-2"))
    assert seam.kicked("topo-a") == 1
    assert any(e.kind == "stage.idempotent-skip" for e in controller.timeline("R1"))


async def test_out_of_order_events_reach_the_correct_stage() -> None:
    seam = _Seam(park_all=True)  # park each stage so completing one does not cascade
    controller = _controller(_linear_graph(), seam)
    # Deliver the stage-b entry event *before* stage-a's — routing is by `when`, not arrival.
    await controller.handle_event(InboundEvent("R1", "e.b", "src-b"))
    assert seam.calls[0].stage_id == "b"
    await controller.handle_event(InboundEvent("R1", "e.a", "src-a"))
    assert any(c.stage_id == "a" for c in seam.calls)


async def test_reconciliation_recovers_a_dropped_event() -> None:
    seam = _Seam(gated=("oms-design",))
    source: dict[str, set[str]] = {}
    controller = _controller(
        _real_graph(),
        seam,
        external_events=("build.ready-in-qa",),
        source_state=lambda rid: source.get(rid, set()),
    )
    await _drive_to_build_wait(seam, controller, "R1")
    # The build.ready-in-qa webhook was dropped: SIT never started via the fast path.
    assert seam.kicked("oms-sit") == 0
    # Source systems show the build is ready; reconciliation delivers the missing event.
    source["R1"] = {"build.ready-in-qa"}
    await controller.reconcile("R1")
    assert seam.kicked("oms-sit") == 1
    assert controller.saga("R1").status == "done"


async def test_reconciliation_is_idempotent() -> None:
    seam = _Seam(gated=("oms-design",))
    source = {"R1": {"build.ready-in-qa"}}
    controller = _controller(
        _real_graph(),
        seam,
        external_events=("build.ready-in-qa",),
        source_state=lambda rid: source.get(rid, set()),
    )
    await _drive_to_build_wait(seam, controller, "R1")
    await controller.reconcile("R1")
    await controller.reconcile("R1")  # second pull must not re-run SIT
    assert seam.kicked("oms-sit") == 1


# --------------------------------------------------------------------------------------------
# Locking
# --------------------------------------------------------------------------------------------


async def test_two_requirements_serialise_on_a_shared_contract() -> None:
    seam = _Seam(gated=("oms-design",))
    controller = _controller(_real_graph(), seam, external_events=("build.ready-in-qa",))
    # R1 reaches design and parks on the gate, holding the contract locks.
    await controller.handle_event(InboundEvent("R1", "requirement.created", "j1"))
    assert controller.saga("R1").held_locks
    # R2 reaches design but the contract is held -> it parks on the lock (no design run yet).
    await controller.handle_event(InboundEvent("R2", "requirement.created", "j2"))
    r2 = controller.saga("R2")
    assert r2.status == "parked"
    assert r2.pending_lock_stage == "design"
    assert seam.kicked("oms-design") == 1  # only R1's design ran so far
    # R1's gate resolves -> contract released -> R2 resumes and acquires it.
    await controller.resolve_gate("R1", approved=True)
    assert controller.saga("R2").held_locks
    assert seam.kicked("oms-design") == 2


async def test_parked_on_lock_consumes_no_run() -> None:
    seam = _Seam(gated=("oms-design",))
    controller = _controller(_real_graph(), seam, external_events=("build.ready-in-qa",))
    await controller.handle_event(InboundEvent("R1", "requirement.created", "j1"))
    await controller.handle_event(InboundEvent("R2", "requirement.created", "j2"))
    before = len(seam.calls)
    # R2 sits parked on the lock; no further seam calls happen while it waits.
    assert controller.saga("R2").status == "parked"
    assert len(seam.calls) == before


async def test_locks_disjoint_contracts_acquire_in_parallel() -> None:
    locks = LockManager()
    assert locks.try_acquire("R1", ("contract:a",)) is True
    assert locks.try_acquire("R2", ("contract:b",)) is True  # disjoint -> both proceed


async def test_locks_are_all_or_none() -> None:
    locks = LockManager()
    assert locks.try_acquire("R1", ("contract:a",)) is True
    # R2 wants a + b; a is held -> takes neither (b must stay free).
    assert locks.try_acquire("R2", ("contract:a", "contract:b")) is False
    assert locks.holder("contract:b") is None
    # Releasing a unblocks R2, which can now take both.
    resumed = locks.release("R1", ("contract:a",))
    assert resumed == ["R2"]
    assert locks.try_acquire("R2", ("contract:a", "contract:b")) is True


async def test_locks_crossed_need_does_not_deadlock() -> None:
    locks = LockManager()
    assert locks.try_acquire("R1", ("contract:a",)) is True
    assert locks.try_acquire("R2", ("contract:b",)) is True
    # Crossed need: neither can take both, and — critically — neither holds a partial set.
    assert locks.try_acquire("R1", ("contract:a", "contract:b")) is False
    assert locks.try_acquire("R2", ("contract:a", "contract:b")) is False
    # Fixed-order acquisition + all-or-none means releasing frees the tangle deterministically.
    locks.release("R1", ("contract:a",))
    assert locks.try_acquire("R2", ("contract:a", "contract:b")) is True


# --------------------------------------------------------------------------------------------
# Failure vs wait
# --------------------------------------------------------------------------------------------


async def test_parked_on_gate_consumes_no_run() -> None:
    seam = _Seam(gated=("oms-design",))
    controller = _controller(_real_graph(), seam, external_events=("build.ready-in-qa",))
    await controller.handle_event(InboundEvent("R1", "requirement.created", "j1"))
    assert controller.saga("R1").status == "parked"
    before = len(seam.calls)
    # A gate wait is cheap persisted state — no running process, no further seam calls.
    assert len(seam.calls) == before
    assert seam.kicked("oms-intake") == 1
    assert seam.kicked("oms-design") == 1


async def test_failure_is_retried_idempotently_without_double_advancing() -> None:
    seam = _Seam(fail_plan={"topo-b": 1})  # b fails once, then completes
    controller = _controller(_linear_graph(), seam)
    await controller.handle_event(InboundEvent("R1", "e.a", "src-a"))
    assert seam.kicked("topo-b") == 2  # one failure + one success
    assert seam.kicked("topo-c") == 1  # advanced exactly once, no double-advance
    assert controller.saga("R1").status == "done"


async def test_repeated_failure_surfaces_to_a_human() -> None:
    surfaced: list[SurfaceNotice] = []
    seam = _Seam(fail_plan={"topo-a": 99})  # always fails
    controller = _controller(_linear_graph(), seam, on_surface=surfaced.append, max_attempts=3)
    await controller.handle_event(InboundEvent("R1", "e.a", "src-a"))
    assert seam.kicked("topo-a") == 3  # bounded retries
    assert seam.kicked("topo-b") == 0  # never advanced
    assert controller.saga("R1").status == "failed"
    assert len(surfaced) == 1
    assert "repeatedly failed" in surfaced[0].reason


async def test_gate_rejection_is_terminal_and_releases_locks() -> None:
    seam = _Seam(gated=("oms-design",))
    surfaced: list[SurfaceNotice] = []
    controller = _controller(
        _real_graph(),
        seam,
        external_events=("build.ready-in-qa",),
        on_surface=surfaced.append,
    )
    await controller.handle_event(InboundEvent("R1", "requirement.created", "j1"))
    await controller.resolve_gate("R1", approved=False, detail="design insufficient")
    r1 = controller.saga("R1")
    assert r1.status == "failed"
    assert not r1.held_locks  # contract freed on rejection
    assert seam.kicked("oms-build") == 0  # did not advance
    assert len(surfaced) == 1


# --------------------------------------------------------------------------------------------
# Cancellation + compensation
# --------------------------------------------------------------------------------------------


async def test_cancel_releases_locks_and_closes_the_gate() -> None:
    seam = _Seam(gated=("oms-design",))
    closed: list[tuple[str, str]] = []
    controller = _controller(
        _real_graph(),
        seam,
        external_events=("build.ready-in-qa",),
        on_close_gate=lambda rid, gate: closed.append((rid, gate)),
    )
    await controller.handle_event(InboundEvent("R1", "requirement.created", "j1"))
    assert controller.saga("R1").held_locks  # parked on gate, holding the contract
    await controller.cancel("R1")
    r1 = controller.saga("R1")
    assert r1.status == "cancelled"
    assert not r1.held_locks
    assert ("R1", "oms-design-gate") in closed


async def test_cancel_runs_compensations_in_reverse_order() -> None:
    seam = _Seam(gated=("oms-design",))
    controller = _controller(_real_graph(), seam, external_events=("build.ready-in-qa",))
    # Advance R1 past intake, design, and build (now waiting on external CI).
    await _drive_to_build_wait(seam, controller, "R1")
    assert controller.saga("R1").passed_stages == ["intake", "design", "build"]
    await controller.cancel("R1")
    # Compensations run for passed stages that declare one, in reverse: build then design.
    comps = [c.topology for c in seam.calls if c.is_compensation]
    assert comps == ["oms-compensate-build", "oms-compensate-design"]
    assert controller.saga("R1").status == "cancelled"


async def test_cancel_releases_lock_and_lets_a_waiter_proceed() -> None:
    seam = _Seam(gated=("oms-design",))
    controller = _controller(_real_graph(), seam, external_events=("build.ready-in-qa",))
    await controller.handle_event(InboundEvent("R1", "requirement.created", "j1"))
    await controller.handle_event(InboundEvent("R2", "requirement.created", "j2"))
    assert controller.saga("R2").pending_lock_stage == "design"
    await controller.cancel("R1")  # frees the contract R2 is queued on
    assert controller.saga("R2").held_locks
    assert seam.kicked("oms-design") == 2


# --------------------------------------------------------------------------------------------
# Correlation + defect loop
# --------------------------------------------------------------------------------------------


async def test_every_run_carries_the_requirement_id_and_timeline_correlates() -> None:
    seam = _Seam(gated=("oms-design",))
    controller = _controller(_real_graph(), seam, external_events=("build.ready-in-qa",))
    await _drive_to_build_wait(seam, controller, "OMS-77")
    assert all(c.requirement_id == "OMS-77" for c in seam.calls)
    timeline = controller.timeline("OMS-77")
    assert timeline
    assert all(e.requirement_id == "OMS-77" for e in timeline)
    assert [e.seq for e in timeline] == sorted(e.seq for e in timeline)


async def test_defect_loop_routes_to_build_then_sit() -> None:
    seam = _Seam(gated=("oms-design",))
    controller = _controller(_real_graph(), seam, external_events=("build.ready-in-qa",))
    await _drive_to_build_wait(seam, controller, "R1")
    assert seam.kicked("oms-build") == 1
    # A defect raised in QA routes back to build (loop re-entry re-runs the passed stage).
    await controller.handle_event(InboundEvent("R1", "defect.raised", "sast-1"))
    assert seam.kicked("oms-build") == 2
    assert any(e.kind == "loop.reentry" for e in controller.timeline("R1"))
    # Its fix routes to sit.
    await controller.handle_event(InboundEvent("R1", "defect.fixed", "dev-1"))
    assert seam.kicked("oms-sit") == 1


async def test_events_on_a_terminal_saga_are_ignored() -> None:
    seam = _Seam(gated=("oms-design",))
    source = {"R1": {"build.ready-in-qa"}}
    controller = _controller(
        _real_graph(),
        seam,
        external_events=("build.ready-in-qa",),
        source_state=lambda rid: source.get(rid, set()),
    )
    await _drive_to_build_wait(seam, controller, "R1")
    await controller.reconcile("R1")
    assert controller.saga("R1").status == "done"
    before = len(seam.calls)
    await controller.handle_event(InboundEvent("R1", "defect.raised", "late"))
    assert len(seam.calls) == before  # a done saga does not react


# --------------------------------------------------------------------------------------------
# Integration: the real StageRunner as the seam
# --------------------------------------------------------------------------------------------


async def test_real_stage_runner_wired_as_the_seam() -> None:
    """Prove the seam integrates a real bounded SwarmKit stage run (judge -> approval)."""
    from swarmkit_runtime.governance import DecisionSkillResult, PolicyDecision  # noqa: PLC0415
    from swarmkit_runtime.governance._approval import ApprovalPolicy  # noqa: PLC0415
    from swarmkit_runtime.governance._mock import MockGovernanceProvider  # noqa: PLC0415
    from swarmkit_runtime.langgraph_compiler._stage_runner import StageRunner  # noqa: PLC0415
    from swarmkit_runtime.resolver import ResolvedAgent, resolve_workspace  # noqa: PLC0415
    from swarmkit_runtime.review import FileReviewQueue  # noqa: PLC0415
    from swarmkit_runtime.review._multiparty import (  # noqa: PLC0415
        open_gate,
        role_task_item_id,
    )

    ws = resolve_workspace(_WS)
    stages: list[ResolvedAgent] = list(ws.topologies["oms-stage-run"].root.children)
    funnel_spec = dict(ws.funnels["oms-design-gate"].spec)

    class _Gov(MockGovernanceProvider):
        def __init__(self) -> None:
            super().__init__(allow_all=True)

        async def evaluate_decision_skill(self, **kw: Any) -> DecisionSkillResult:
            return DecisionSkillResult(
                skill_id=kw.get("skill_id", ""),
                verdict="pass",
                confidence=0.95,
                reasoning="meets rubric",
            )

        async def evaluate_action(self, **kw: Any) -> PolicyDecision:
            return PolicyDecision(allowed=False, reason="out of scope", tier=0)

    async def agent_runner(agent: ResolvedAgent, prior: str, critique: str | None) -> str:
        return f"<{agent.id} artifact from {prior[:20]!r}>"

    with tempfile.TemporaryDirectory() as tmp:
        queue = FileReviewQueue(Path(tmp))
        gate_id = "INT-1:designer"
        open_gate(
            queue,
            gate_id=gate_id,
            topology_id="INT-1",
            agent_id="designer",
            policy=ApprovalPolicy.from_dict(funnel_spec["approve"]),
        )
        queue.record_resolution(role_task_item_id(gate_id, 0, "oms-lead"), "approved", "alice")
        queue.record_resolution(role_task_item_id(gate_id, 0, "web-lead"), "approved", "bob")

        runner = StageRunner(
            governance=_Gov(),
            review_queue=queue,
            role_registry=ws.role_registry,
            agent_runner=agent_runner,
            max_wait_seconds=1,
        )

        seen_ids: list[str] = []

        async def run_stage(request: StageRunRequest) -> StageRunOutcome:
            seen_ids.append(request.requirement_id)
            result = await runner.run(
                stages, correlation_id=request.requirement_id, initial_input="BRD"
            )
            return StageRunOutcome(status=result.status)

        # A one-stage graph whose single stage drives the real StageRunner.
        graph = StageGraph(
            "integration",
            [Stage("run", "oms-stage-run", ("requirement.created",), None, (), None, None, None)],
            [],
        )
        controller = PipelineController(graph, run_stage)
        await controller.handle_event(InboundEvent("INT-1", "requirement.created", "j1"))

    assert seen_ids == ["INT-1"]  # correlation id threaded into the real run
    assert controller.saga("INT-1").status == "done"
