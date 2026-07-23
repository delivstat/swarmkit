# Level 16: Pipelines & Contracts

Sequence multiple bounded topology runs into a long-running delivery pipeline — with human gates, integration locks, and recovery from dropped events. This is a different orchestration axis from Level 12's triggers and canary: those *start* runs, a pipeline *sequences* them.

## What you'll learn

- **StageGraph** — a whole pipeline authored as one data file (`kind: StageGraph`)
- The **controller / saga** model: SwarmKit runs each bounded stage; the controller owns the weeks-long state, dedup, reconciliation, and compensation
- **Contracts** — turning a stage's `locks:` into a real, checked vocabulary
- Gating a stage on a **Funnel**
- The pluggable **orchestration seam**: a reference controller vs. Temporal

## The idea

A pipeline is a **saga**: a requirement moves through stages, each of which is a bounded SwarmKit run. The application (a controller) owns the durable, weeks-long state — SwarmKit only runs the governed determinations inside each stage. This is the same split the [SDLC pipeline example](../sdlc-example/) is built on.

## Build it

A `StageGraph` wires stages by the events that enter (`when`) and leave (`success`) them. A stage can hold integration `locks`, block on a `gate` (a [Funnel](../reference/funnel.md)), and declare a `compensation` topology for rollback:

```yaml
apiVersion: swarmkit/v1
kind: StageGraph
metadata:
  id: oms-pipeline
  name: OMS delivery pipeline
stages:
  - id: intake
    topology: oms-intake
    when: [requirement.created]
    success: design.kickoff
  - id: design
    topology: oms-design
    when: [design.kickoff]
    locks: [oms-inventory, oms-web]   # integration contracts — all-or-none
    gate: oms-design-gate             # a Funnel; blocks on human approval
    success: design.approved
    release_locks_on: design.approved
    compensation: oms-compensate-design
  - id: build
    topology: oms-build
    when: [design.approved]
    success: build.ready-in-qa        # an EXTERNAL event (CI webhook)
  - id: sit
    topology: oms-sit
    when: [build.ready-in-qa]
    success: sit.passed
loops:
  - when: defect.raised               # a defect routes back to build
    to: build
  - when: defect.fixed
    to: sit
provenance:
  authored_by: human
  version: 1.0.0
```

!!! note "The entry field is `when`, not `on`"
    YAML 1.1 coerces a bare `on:` key to the boolean `true`, so the stage-entry field is `when`.

Each lock is a first-class **[Contract](../reference/contract.md)** (`kind: Contract`) naming the apps it binds — so two requirements that both touch the OMS↔Web order API can never design concurrently; the second parks and resumes.

## Run it

The example ships self-contained, deterministic demos (no API key, no server):

```bash
just demo-pipeline-controller    # the saga: duplicate / dropped / contended / cancelled
just demo-pipeline-temporal      # the same pipeline on Temporal, in-process
```

The reference controller is the zero-infra option; a **Temporal** adapter implements the same `OrchestrationProvider` interface for production — one data-driven workflow interprets any StageGraph, so the graph stays data and you swap the engine underneath it.

## What happened

The controller sequenced a requirement across stages while surviving the real world: a duplicate webhook is deduped, a dropped event is recovered by reconciliation, a contended contract serialises the second requirement, and a cancellation unwinds each passed stage's `compensation` in reverse.

## Learn more

- [StageGraph artifact reference](../reference/stage-graph.md) · [Contract artifact reference](../reference/contract.md) · [Funnel artifact reference](../reference/funnel.md)
- [SDLC pipeline walkthrough](../sdlc-example/) — the whole thing, on video, running in the composer.
