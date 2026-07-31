---
title: One parking mechanism — converging the stage gate and the funnel gate
description: A pipeline parks two different ways. The bundled path parks durably but enforces no approval policy; the funnel path enforces a real multi-party policy but parks by blocking a coroutine that a restart destroys. Neither is both. This note converges them on non-blocking parking, which is what finally makes a bundled pipeline's parked run approvable.
tags: [runtime, pipeline, orchestration, governance, approval]
status: draft
---

# One parking mechanism

**Scope:** `runtime` (`server/_pipeline_stage.py`, `langgraph_compiler/_stage_runner.py`,
`langgraph_compiler/_gate_funnel.py`, `review/_multiparty.py`, `orchestration/reference`)
**Design reference:** §8.5 (GovernanceProvider), §8.7 (reserved-for-human scopes), §14.
Completes `pipeline-gate-approval-ui.md` (slices 1–3 shipped the identity model, the serialization
and the UI); builds on `multi-party-approval.md`, `gate-funnel.md`,
`bundled-pipeline-orchestrator.md`.
**Status:** draft

## Goal

Make a parked run of the **bundled** pipeline resolvable through the approval engine, by giving the
two parking mechanisms one shape: a gate opens, the run persists, and resolution resumes it.

## Non-goals

- **Not changing the approval engine.** `ApprovalPolicy`, `RoleRegistry`, `evaluate`,
  `collect_resolutions` and the quorum model stay as they are.
- **Not changing the drive contract.** `RunStage` / `PipelineSignal` / `StageOutcome` keep their
  shape; `StageOutcome` already carries the optional `artifact_bytes` added in 1.127.0.
- **Not removing `swarmkit pipeline advance`.** It stays as break-glass. What changes is that it
  stops being the *only* way to release a gate.
- **Not a UI change.** The `/runs` panel from slice 3 already renders whatever role-tasks exist; it
  currently finds none on the bundled path. This note is what puts them there.
- **Not Temporal.** The distributed `OrchestrationProvider` is unaffected — it drives the same
  run-stage seam.

## The problem: two mechanisms, neither of them complete

**Path A — the stage-graph gate.** This is what `swarmkit serve` + the bundled orchestrator actually
run. `build_pipeline_run_stage` (`server/_pipeline_stage.py`) runs the stage's topology, stores the
artifact, and returns `StageOutcome(status="parked")` whenever `stage.gate` or `stage.funnel` is
set. The saga persists `status="parked"` and `pending_gate_stage`. Durable, restart-safe, correct —
and it **never calls `open_gate`**. No `ApprovalPolicy` is evaluated, no role-tasks exist, no quorum
is enforced. The only release is an operator emitting the `gate` event under the reserved
`pipeline:advance` scope: a single human act, with none of the multi-party guarantees the funnel
declares.

**Path B — the agent funnel's `approve` layer.** `StageRunner._run_gated_stage`
(`langgraph_compiler/_stage_runner.py:122`) calls `run_agent_funnel_gate`, which reaches
`build_multiparty_approver` and `resolve_multiparty`. Real role-tasks, real quorum, real
segregation-of-duties. But it parks by **blocking**: `resolve_multiparty` holds a coroutine in a
poll loop until the gate resolves or `max_wait_seconds` expires. A restart destroys the wait and the
in-flight run; the review items survive on disk but nothing is watching them. `StageRunner` is wired
only in `examples/sdlc-pipeline` and tests — the bundled serve path does not construct it.

So the product ships **durable parking without a policy** (A), or **a policy that cannot survive a
restart** (B). Slices 1–3 made B's role-tasks resolvable from the CLI and the UI, which is real
progress and does not help a bundled pipeline at all.

There is a third symptom worth naming, because it is the same defect seen from outside:
`GET /pipelines/gate-status` reports on role-tasks that path A never creates, so for the bundled
pipeline it folds an empty list and answers `pending` forever.

## The decision this implements

`pipeline-gate-approval-ui.md` settled the timeout question: **a gate parks until acted on**, with
the timeout retained only for unattended runs, declared as `approve.on_timeout: park | deny`.

That decision cannot be implemented on path B as it stands. `max_wait_seconds=None` does not park —
it leaks a coroutine until the process restarts and then loses the gate entirely. **Durable parking
requires B to stop blocking**, which is the same change as converging it with A.

## The plan

### 1. A non-blocking gate resolution

`resolve_multiparty` splits into the two halves it already contains:

```python
def open_multiparty(...) -> GateHandle:      # open_gate + audit, returns immediately
async def poll_multiparty(...) -> MultiPartyDecision:   # today's bounded poll, unchanged
```

`open_multiparty` is what the pipeline path uses. `poll_multiparty` is retained for the
in-topology, single-process case — a `swarmkit run` of a topology whose agent carries a funnel,
where there is no saga to park into and blocking is the only option. Its timeout keeps the
`on_timeout: deny` semantics for that case, which is exactly the "unattended run" the decision
carved out.

### 2. The funnel gate returns `parked`

`run_agent_funnel_gate` gains a mode (set by the caller, not the artifact): when driven by a
pipeline stage, the `approve` layer opens the gate and returns an outcome of `parked` carrying the
`gate_id`, instead of awaiting a decision. `StageRunner._run_gated_stage` propagates it rather than
blocking.

### 3. Path A opens the policy

`build_pipeline_run_stage` resolves the stage's gate to its `ApprovalPolicy` — the same lookup
`GET /pipelines/gate-status` already does (`_gate_policy`, walking the agent tree by the gate name)
— and calls `open_multiparty` before returning `parked`. This is the change that gives the bundled
pipeline multi-party approval, and it is small precisely because slice 2 already built the lookup.

A stage whose `gate` names a funnel with **no `approve` layer** keeps today's behaviour: it parks
with no role-tasks and releases via `pipeline:advance`. That is a legitimate configuration — a gate
can be a checkpoint rather than a vote — and it must not start failing.

### 4. Resolution emits the `gate` event

The last role-task resolving is what resumes the run. `POST /review/{id}/resolve` (and the CLI verb)
gains a post-commit step: re-evaluate the gate, and when it reaches APPROVED or REJECTED, deliver
`{"kind": "gate", "approved": …, "stage": …}` through the existing `PipelineSignal` sink.

This is the piece that makes `swarmkit pipeline advance` revert to what it always should have been.
Note the asymmetry to preserve: `advance` remains a **reserved-scope operator act** that bypasses
the policy, while a resolution-driven gate event is the *policy being satisfied*. Both must be
distinguishable on the audit — the first is `pipeline.ingress` with `mode=advance`, the second is
`approval.gate_resolved` followed by an `emit`.

### 5. `gate-status` stops needing its fallback

Once path A opens policies, `quorum_evaluated: false` becomes rare rather than the norm for bundled
pipelines. Keep the fallback — an externally-driven gate is still legitimate — but the common case
becomes the correct one.

## API shape

```python
# review/_multiparty.py — the split
@dataclass(frozen=True)
class GateHandle:
    gate_id: str
    role_tasks: tuple[str, ...]      # item ids, for a caller that wants to report them

def open_multiparty(
    queue: ReviewQueue, *, gate_id: str, topology_id: str, agent_id: str,
    policy: ApprovalPolicy, governance: GovernanceProvider,
) -> GateHandle: ...

async def poll_multiparty(...) -> MultiPartyDecision: ...   # today's signature, unchanged

# server/_pipeline_stage.py — path A gains the policy
StageOutcome(status="parked", artifact=ref, artifact_bytes=len(output.encode()), gate_id=gate_id)

# server/_routes_review.py — resolution resumes the run
#   after record_resolution: evaluate(); on APPROVED/REJECTED, signal the gate event.
```

```
swarmkit pipeline advance <cid> <stage>    # unchanged: break-glass, pipeline:advance
swarmkit review resolve <id> --as <who>    # now ALSO resumes the run when it completes the gate
```

## Test plan

- **Unit.** `open_multiparty` returns without awaiting and creates one item per role-task;
  `poll_multiparty` keeps today's behaviour including timeout→deny; a stage whose gate has no
  `approve` layer still parks with zero role-tasks.
- **Integration — the convergence itself.** Drive a bundled pipeline to a gated stage: assert
  role-tasks exist (they do not today), resolve one (saga stays `parked`), resolve the second,
  assert the gate event is delivered and the saga advances.
- **Restart survival — the point of the whole note.** Park a run, **drop and rebuild the process
  state**, resolve the gate, assert the run resumes. Path B cannot pass this today; it is the
  acceptance test.
- **Audit distinguishability.** A run released by `pipeline:advance` and one released by quorum must
  be separable from the audit alone.
- **Full pipeline.** Per the always-test-the-full-pipeline rule: live `swarmkit serve` +
  `swarmkit orchestrator` over `examples/sdlc-pipeline`, parked on a real gate, released by two
  `swarmkit review resolve` calls under two identities.

## Demo plan

`just demo-pipeline-approval` — extend the slice-1 demo to the bundled path:

1. `swarmkit pipeline emit` drives a run to a gated stage.
2. `swarmkit review list --kind role_task` shows role-tasks **for a bundled pipeline** — the thing
   that produces nothing today.
3. **Restart the orchestrator**, to show the park survived.
4. Two `swarmkit review resolve … --as` calls; the first leaves it parked, the second releases it.
5. `swarmkit pipeline status` shows the stage passed, and the audit shows both
   `approval.role_task_resolved` events, `approval.gate_resolved`, and the resulting emit.

Plus the `/runs` panel rendering role-tasks on a bundled run — the slice-3 screenshot, against the
path a real deployment uses.

## Open questions

- **Where does the mode come from?** §2 says "set by the caller, not the artifact" — a funnel should
  not have to know whether it is being run inside a pipeline. But `run_agent_funnel_gate` is reached
  through several layers; threading a flag down may be uglier than giving the funnel gate a
  `park_handler` seam that defaults to the blocking poll. Decide before implementing.
- **Re-opening a gate on a stage retry.** `open_gate` is idempotent and deliberately leaves
  already-submitted items alone, so a retried stage inherits approvals cast against the *previous*
  artifact. That is arguably wrong — the artifact changed — but invalidating them silently discards
  human decisions. Neither behaviour is obviously right; today's is at least documented.
- **`swarmkit run` of a pipeline-shaped topology.** With B non-blocking in the pipeline case and
  blocking otherwise, there are two behaviours for one artifact. Acceptable if the boundary is
  "driven by a saga or not", but it needs to be stated in the funnel reference.
