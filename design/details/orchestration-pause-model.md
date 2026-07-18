# Orchestration and pauses: who waits, who resumes

**Status:** reference (clarifies an existing boundary; no new decision)

A recurring point of confusion: if SwarmKit gates are LangGraph checkpoints that pause for weeks,
why is there an *external* controller at all — and does the controller drive the approval pauses?
This note settles it. It applies to any long-running, multi-stage SwarmKit deployment; the SDLC
pipeline (`sdlc-pipeline-example.md`, `pipeline-controller.md`) is the worked example.

The answer: **there are two different kinds of pause, handled by two different layers. The
controller does not drive approval pauses.**

## The two kinds of pause

| Pause | Handled by | Who resumes it | Example |
| --- | --- | --- | --- |
| **Human approval *within* a stage** | **SwarmKit checkpoint** (`interrupt()` + checkpointer) — a persisted DB row, no running process | a **human** approving (`Command(resume)`) | the design gate waiting on app leads + InfoSec |
| **Transition *between* stages** | **the external controller** | an **external system event** (CI / Jira / Git webhook), or a stage's own completion signal | waiting for CI's `build.ready-in-qa` before SIT |

## The rule

- **Within a stage:** SwarmKit owns the pause and a human resumes it. The controller is *idle* during
  an approval wait — it does not poll or drive it. Its only involvement is being **notified that the
  gate resolved** (the gate-resolution seam in `pipeline-controller.md`), so it knows the stage
  finished and can start the next one. **The controller learns *that* a gate resolved; it never *is*
  the gate.**
- **Between stages:** the controller owns the transition, and it is usually an external event.
  SwarmKit's `interrupt/resume` is driven by a human/caller — it does **not** subscribe to Jira / CI /
  Git, so something must receive those webhooks and kick the next run. That something is the controller.

So **not every pause is triggered by the orchestrator** — approvals are pure SwarmKit checkpoints,
resumed by humans.

## Timeline

```
Jira: requirement created
  → controller kicks the INTAKE run (SwarmKit)
      agents produce impact analysis, run completes            ← no gate
  → controller kicks the DESIGN run (SwarmKit)
      agents draft + synthesize, run hits the APPROVAL GATE
      → SwarmKit interrupt(): persist to DB, return            ← the pause is a DB row
          ...days pass, NO process running, controller idle...
      → app leads + InfoSec approve  →  Command(resume) to SwarmKit
      → quorum met, gate passes, DESIGN run COMPLETES
  → controller is *notified* the gate resolved → kicks the BUILD run
      build produces a candidate diff, run completes
  → controller waits for CI's external "build.ready-in-qa" webhook  ← external pause
      → webhook arrives → controller kicks the SIT run ...
```

The days-long approval wait is entirely SwarmKit's checkpoint, resumed by humans. The CI wait is an
external event only the controller can catch.

## Why not one long SwarmKit run (with an interrupt at every stage)?

Fair question — checkpoints *can* pause for weeks. Two reasons the controller must exist regardless,
and one reason we still split into per-stage runs:

1. **External events aren't human resumes.** "Build ready", "defect fixed", "ticket transitioned"
   come from CI / Git / Jira. SwarmKit's `interrupt/resume` doesn't listen to webhooks — something has
   to receive them and resume/kick. That is the controller, one-big-run or not.
2. **State-schema drift.** A single run pins the graph definition it was created against; over weeks
   the topology changes (new archetype, revised skill), and resuming a months-old checkpoint against a
   changed graph is fragile. Per-stage runs each use the *current* definition and close cleanly.
3. **Clean audit + bounded runs.** A stage that closes gives a queryable "done" with its own cost, vs
   one span open for a quarter.

Even in the hypothetical mega-run you would still need the controller for external events; we simply
also chose bounded per-stage runs over one long one.

## One-line model

> Determination + governance + the human-approval *pause* live **inside** SwarmKit (checkpoints,
> resumed by humans). Sequencing **across** stages, driven by external system events, is the
> **controller**. The controller learns *that* a gate resolved; it never *is* the gate.

## Where this applies

- `design/details/pipeline-controller.md` — the controller + the gate-resolution notification seam.
- `design/details/multi-party-approval.md`, `design/details/gate-funnel.md` — gates as
  checkpoint-backed interrupts (the intra-stage pause).
- `design/details/sdlc-pipeline-example.md` — the orchestration boundary in context.
