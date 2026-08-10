# Reading a gate, and approving without a saga

**Status:** proposed — design only.

Implements the two items from [`finishing-the-orchestration-seam.md`](finishing-the-orchestration-seam.md)
that block an application owning its own sequencing. Everything else in that note can wait; these
cannot, because without them an application that drops the saga also drops enforced human approval.

---

# Part 1 — `GET /gates/{gate_id}`

## Goal

Let a caller ask *"is this gate resolved?"* and get the answer **with the approval policy already
applied**.

## Why it is not just a review-queue filter

`GET /review?gate_id=…` returns the individual role-tasks. Turning those into a decision means
applying quorum, distinct-approver counts and `exclude_author` — which lives in `evaluate()` and
`collect_resolutions()` and is exactly the SwarmKit-shaped part an application must not rebuild.

Without this endpoint every external driver either reimplements the approval policy or approximates
it, and an approximation of an approval policy is a governance failure with a friendly name. It is
also the one thing a client genuinely cannot derive: the policy lives in the funnel, which the client
does not read.

## Shape

```
GET /gates/{gate_id}
→ {
    "gate_id": "wms-design:designer",
    "status": "pending" | "approved" | "rejected",
    "policy": {"scope": "design:approve", "roles": ["oms-lead"], "quorum": "all",
               "exclude_author": true, "min_distinct_approvers": 2},
    "resolutions": [{"item_id": "...", "status": "approved", "resolved_by": "...", "role": "..."}],
    "distinct_approvers": ["alice"],
    "artifact_ref": "WMS-35/<run>/output"
  }
```

`status` is the only field a driver must understand; the rest is for a human reading why.

Backed by a pure function — `gate_state(queue, registry, policy, gate_id) -> GateState` — so the
endpoint, a CLI command and a test all ask one implementation, as `gate_coverage` does.

### Resolving the policy

The gate id determines it, and there are two shapes in use:

- **funnel-on-agent:** `"{topology_id}:{agent_id}"` → topology → agent → funnel → `approve` block.
- **stage gate:** the `gate:` value is a funnel id → funnel → `approve` block.

Both resolve deterministically from the workspace. A gate id matching neither is a 404 rather than a
guess.

### CLI

`swarmkit review gate <gate-id>` — under `review`, not `gates`, because `swarmkit gates` is pipeline
gate *coverage* (a static analysis) and this is a live queue question. The naming collision is
unfortunate and worth a second opinion.

## Non-goals

- Not a webhook. Polling is adequate and the ADR already lists callbacks as pending.
- Not resolving gates. `POST /review/{id}/approve` already does that.
- Not changing quorum semantics.

---

# Part 2 — the funnel's approve layer defers

## Goal

Human approval that works whether or not a saga exists — under the bundled controller, under
Temporal, or under a shell script.

## What changes

`build_advisory_approver` records and passes, justified in 1.172.0 as *"human approval is the
stage-level `gate:`, which parks the saga durably"*. With sequencing in the application there may be
no saga and no stage gate, so approval would be unavailable on exactly the path being recommended.

It was already the weaker branch. The choice was framed as *block the coroutine for seven days* or
*pass advisorily*; rejecting the block was right, but **defer-and-resume already existed** —
`HITLDeferredError` checkpoints, closes the job `deferred`, exits cleanly, and
`swarmkit run --resume` continues after `swarmkit review approve`.

## The part the earlier note missed: resume re-runs the node

LangGraph checkpoints at super-step boundaries. A node that raises is re-run on resume — so a naive
defer would **re-draft the artifact** after approval. On `wms-design` that is ~$2.40 per approval,
and worse: the human would have approved an artifact that no longer exists.

So the gated node has to be **re-entrant**:

```
on entry to the gated node:
    gate  = "{topology_id}:{agent_id}"
    state = gate_state(gate)                  # Part 1's function, in-process

    approved  → return the artifact stored on the gate; produce nothing
    rejected  → return [GATE REJECTED] with the resolver's comment; produce nothing
    pending   → defer again; produce nothing
    absent    → produce → validate → judge → open the gate → defer
```

Only the last branch runs the agent. "Resuming while still unapproved defers again" stops being a
test case and becomes the same code path as the first defer.

The artifact is already stored on the review item (`open_gate` posts it with `artifact_ref`), so no
new storage is needed — only the branch.

**Edge case, stated:** if the gate is absent on re-entry because the item was purged, the node
produces again. Acceptable and cheaper than the alternatives; worth a log line so it is not silent.

## The gate id is not unique per run

Two conventions exist, and only one of them is correct:

```
_stage_runner.py:138   gate_id = f"{correlation_id}:{agent.id}"     # run-unique
_compiler.py:954       gate_id = f"{topology_id}:{agent.id}"       # NOT run-unique
```

The in-node id carries no correlation and no run. In the model this design is for — **independent
runs connected by a correlation id, not stages of a pipeline** — every run of `wms-design` produces
the gate id `wms-design:designer`. Two tickets in flight would share review items, have quorum
counted across both, and approving one would release the other.

It is latent today only because the in-node approve is advisory. **Turning it on without fixing this
ships a governance bug**: an approval granted for one requirement satisfying a different one.

### The fix, and the convention question

The in-node gate id becomes **`"{run_id}:{agent_id}"`**. The run id is already in the run scope
(`_run_scope`, 1.175.0), it is `jobs.id`, and a driver holds it from the `POST /run/{topology}`
response — so it stays derivable without the driver knowing anything new.

Run id rather than correlation id is deliberate. A correlation groups *several* runs (a retry, a
second attempt at the same ticket), and a gate keyed on it would let approvals cast against a
previous artifact satisfy a new one. `open_gate` already documents that hazard for retried stages —
*"those approvals were cast against the PREVIOUS artifact, which is arguably wrong"* — and this is
the chance not to inherit it.

That leaves two conventions in the codebase, which is what caused the problem. **Recommendation:
unify on `"{run_id}:{agent_id}"` everywhere.** A stage's run id is already `<correlation>:<stage>`,
so a stage gate becomes `WMS-27:design:designer` — still correlation-bearing, and run-unique for
free. It is a breaking change for gates open at upgrade time, which is the only reason not to.

## The compatibility problem: double gating

A pipeline stage with `gate:` whose agent *also* has a funnel with `approve:` would gate **twice** —
once in-node, once at the stage. Today only the stage gate fires, because in-node is advisory.

This only arises for stage runs. The model this design serves — independent runs joined by a
correlation id — has no stage and no `gate:`, so the in-node layer is the *only* gate and must
enforce.

**Decision: suppress the in-node approve for pipeline-stage runs.** `_pipeline_stage` already opens
the gate and returns `parked`; it passes a flag saying so, and the in-node layer stays advisory on
that path. Existing pipelines behave byte-identically; independent runs gain enforced approval,
which is the whole point.

No workspace flag. A configuration switch here would be one more thing that can be declared and not
read, and the distinction (is this run a pipeline stage?) is known at the call site.

## Serve does not handle deferral

`HITLDeferredError` is caught only in the CLI. Under `swarmkit serve` a deferred run currently
surfaces as a **failed job** — so this must be handled there too: job status `deferred`, the gate id
in the error field, and the existing resume path reachable.

That implies `POST /jobs/{job_id}/resume` (item 3 of the seam note) lands with this rather than
after it, or serve can defer and never continue.

## What is required to wire it

- `review_queue` and `role_registry` passed into `compile()` again, for the approve layer only. The
  1.172.0 guard removal stays correct — `validate` and `judge` must never depend on a queue.
- `WorkspaceRuntime.compile()` supplies both; it already resolves the workspace and the review queue
  is a filesystem/queue object it can construct.

## Non-goals

- Not removing `gate:` from the stage schema.
- Not changing what `validate` or `judge` do.
- Not making approval mandatory. A funnel with no `approve` block is unaffected.

---

## Test plan

**Part 1**
- Quorum applied: two of three roles approved with `quorum: all` reads `pending`, not `approved`.
- `exclude_author` honoured: the author's own approval does not count.
- A rejection anywhere reads `rejected`.
- Both gate-id shapes resolve their policy; an unknown gate 404s.
- **Two concurrent runs of the same topology produce two distinct gates**, and approving one leaves
  the other pending — the defect this design would otherwise ship.
- The endpoint and the CLI return the same verdict for the same queue state.

**Part 2**
- A run whose funnel gate is unresolved closes as `deferred`, writes review items, holds no process.
- **Resuming after approval completes the run and calls the model provider ZERO further times** —
  the assertion the re-entrancy exists for.
- Resuming while still pending defers again, and still does not call the provider.
- A rejection returns a gate-rejected result carrying the resolver's comment.
- A pipeline stage with `gate:` gates exactly once, and its saga timeline is unchanged from 1.180.0.
- A funnel with no `approve` block never defers.
- Serve records `deferred` rather than `failed`, and the job resumes over HTTP.
- Parity: the same funnel-gated topology approves identically under the bundled controller, under a
  bare `swarmkit run` loop, and over HTTP.

## Demo plan

A one-shot run against a funnel-gated topology:

```
$ swarmkit run ws wms-design -i "…" --correlation-id WMS-35
⏸ Review deferred: gate wms-design:designer awaits approval
  1. Approve: swarmkit review approve <id>
  2. Resume:  swarmkit run ws wms-design --resume

$ swarmkit review gate wms-design:designer
pending — 1 of 2 distinct approvers (needs oms-lead)

$ swarmkit review approve <id> && swarmkit run ws wms-design --resume
[designer] resumed from gate (no re-draft)
```

Plus the cost line before and after resume, showing the second half added nothing.

## Open questions for review

1. **`swarmkit review gate` vs extending `swarmkit gates`.** Coverage and live state are different
   questions; putting them under one noun may still be less confusing than two.
2. **Should a rejected gate fail the run or return a rejected artifact?** Today's gated node returns
   `[GATE REJECTED]`, which a caller may treat as output. For a driver, a non-zero exit is easier to
   branch on.
3. **Does the pipeline-stage suppression flag belong on the run request or in the compile?** Call
   site is cleaner; a run-level field is more visible in the audit record.
4. **Unify the gate-id convention now, or leave the stage path alone?** One rule is better than
   two — two is what produced the collision above — but changing the stage path breaks gates open at
   upgrade time.
5. **Should `deferred` be terminal for the job row, or a distinct resumable state?** It closes the
   row today, which makes "how many runs are waiting on a human" a query over `status='deferred'` —
   convenient, but it conflates "finished" and "paused" in the same column.
