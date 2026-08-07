# Reconciliation does not advance past a human gate

**Status:** implemented — swarmkit-runtime 1.171.0. Fixes a defect in the bug-20 fix (1.168.0).

## Goal

An orchestrator restart must not convert a gated pipeline into an ungated one.

## What went wrong

The bug-20 fix absorbs a stage that completed while the orchestrator was down: the job row says
`completed`, so the saga records it and `_drive` continues. It handled the stage and not its gate —
`pending_gate_stage` was never set, so a stage declaring `gate:` was marked passed and the next
stage started unreviewed.

```
saga 'WMS-24' was waiting on stage 'triage', which had already completed — absorbing it.

t+  0s ('active', 'triage', '[]',         None)
t+ 20s ('active', 'design', '["triage"]', None)      <- straight to design, no gate
```

No review-queue entry, no `approval.role_task_resolved`, no gate event. The trigger is ordinary: any
restart during a stage — a deploy, an upgrade, an OOM, a reboot — and stages run for minutes, so the
window is wide.

It is the quiet direction of failure. Bug 20's stranding was visible as a run that never progressed.
This looks like a run that went fine, and "who approved this?" answers "nobody, a restart did".

## The fix: refuse, do not park

A gated stage is not absorbed. `_reconcile` looks the stage up in its graph and returns without
touching the saga when it declares `gate:` (or the older `funnel:`), logging why and writing one
`blocked` entry to the saga's timeline.

The alternative — absorb and park — is what the reported symptom asks for, and it cannot be done
here. Parking correctly means *opening* the gate: fanning the funnel's `approve` policy into
role-tasks on the review queue, which needs the workspace funnels, the queue and the artifact, all
serve-side (`server/_pipeline_stage.py`). The controller can only synthesise `completed` from a job
row. Setting `pending_gate_stage` without opening the funnel parks the saga on a gate with no
review-queue entry — a stall nobody can release, traded for a skipped approval.

So a gated stage keeps bug 20's stranding, and a human releases it with `swarmkit pipeline advance`.
The work is not lost: it is in `jobs`, done and paid for. Automating past a human decision is not a
recovery.

An unknown stage — one whose spec cannot be read from the graph — is treated as gated. Deciding on
missing information in the direction that skips reviews is the bug itself.

## Non-goals

- Not a change to bug 20's recovery anywhere else. An ungated stranded stage is still absorbed and
  the run still resumes; that is asserted.
- Not a new store, endpoint or field.

## The follow-up this defers

Reconciling a gated stage *properly* means replaying the post-stage transition where it lives —
a serve-side path that takes a stage whose job already completed and runs its gate-opening without
re-running the topology. That is the general form the report asks for ("replay whatever post-stage
transition the graph declares"), and it is a seam change, not a guard. Worth doing; not worth
shipping under a live pipeline that is currently skipping approvals.

## Test plan

`packages/runtime/tests/test_reconciliation_does_not_pass_a_gate.py` — a gated stage is not marked
passed; the next stage does not run; the saga stays `active` on its stage; the refusal appears once
on the timeline however many times the lease redelivers; `funnel:` gates the same as `gate:`; an
unreadable stage spec is treated as gated; and an ungated stage is still absorbed and still resumes.

## Demo

```
$ uv run pytest packages/runtime/tests/test_reconciliation_does_not_pass_a_gate.py -q
........                                                                 [100%]
8 passed
```

The orchestrator log on the refusal:

```
saga 'WMS-24' is waiting on stage 'triage', which has already completed — NOT absorbing it,
because the stage declares a gate and the approval has not happened. The work is done (see the
job record); release it with `swarmkit pipeline advance WMS-24` after review, or resolve the gate.
```

and `swarmkit pipeline status WMS-24` now carries the reason on the timeline:

```
blocked  triage  stage completed while the orchestrator was down; its gate still needs a human
```
