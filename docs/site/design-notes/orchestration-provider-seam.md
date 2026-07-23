---
title: Orchestration provider seam (delegate pipeline sequencing)
description: Make pipeline sequencing a pluggable provider seam instead of a hand-rolled saga engine. SwarmKit keeps the StageGraph spec, the governed stage run, and the correlated audit; a durable-workflow engine (Temporal, selected) owns state, timers, signals, locking, and compensation.
tags: [runtime, pipeline, orchestration, controller, architecture-decision]
status: proposed
---

# Orchestration provider seam

**Type:** architecture decision (ADR-shaped).
**Scope:** how a `StageGraph` (pipeline) is *executed* — the sequencing/state layer, not the stage.
**Design references:** [`pipeline-controller.md`](pipeline-controller.md) (the reference controller
this generalises), [`executor-abstraction.md`](executor-abstraction.md) (the precedent — delegate
the hard engine, don't rebuild it), [`stage-graph` schema](../reference/stage-graph.md).
**Status:** proposed.

## Context

The reference pipeline controller (slice 5) is a saga engine: durable per-requirement state, event
dedup + reconciliation, per-contract all-or-none locking with queueing, failure-vs-wait retry,
cancellation with reverse-order compensation, and (coming) SLA timers. Every one of those is a
**known-hard distributed-systems problem** that mature engines have spent years hardening. Growing
the hand-rolled controller into a production-grade, durable, multi-week saga engine is a large,
risky, ongoing maintenance commitment that is **not SwarmKit's differentiator**.

This is the same call the [`executor-abstraction`](executor-abstraction.md) note already made for
coding harnesses: *"Rebuilding that scaffolding inside SwarmKit is a multi-year mistake — let a node
delegate to a harness via an adapter."* The identical reasoning applies one layer up: **don't rebuild
Temporal/Camunda inside SwarmKit; delegate durable sequencing to one via a seam.**

## Decision

**Make pipeline orchestration a pluggable provider seam — parallel to `ModelProvider`,
`GovernanceProvider`, and the executor seam — not a bespoke engine SwarmKit owns.**

- SwarmKit **keeps** the three things only it provides:
  1. the **`StageGraph`** as the native, authored, governed pipeline **spec** (topology-as-data at the
     sequencing layer — the engine *interprets* the graph, it is not re-authored in the engine's
     format);
  2. each stage as a **governed SwarmKit topology run** (the bounded, audited, funnel-gated unit);
  3. the **correlated audit** — every stage run stamped with `requirement_id` so the cross-stage DORA
     trail assembles into one record regardless of what sequenced it.
- SwarmKit **delegates** the hard sequencing substrate — durable state, retries, timers, signals,
  cross-run locking, compensation — to an **orchestration engine** behind an `OrchestrationProvider`
  seam.
- The **reference in-memory controller stays** as one implementation of the seam: the zero-infra
  option for demos, tests, and simple self-hosted pipelines. It is proof the seam works, not the
  product.

### Selected engine: Temporal

For the first production adapter, **Temporal** (`temporalio` Python SDK).

Why Temporal fits *this* workload (weeks-long, human-gated, compensating sagas) best:

- **Durable execution** — the workflow *is* the durable state; no bespoke saga store, no reconciliation
  loop. A parked pipeline is a suspended workflow, not a polled DB row.
- **Signals** = human gate resolutions and external events (a webhook signals the workflow); **durable
  timers** = gate/lock SLA + escalation; **queries** = live saga state. Exactly the human-in-the-loop
  primitives the controller hand-rolls.
- **Saga compensation** is a first-class, documented pattern.
- **Python-native** SDK — same language as the runtime.
- **Lightweight where it counts:** tests run fully in-process via the SDK's time-skipping
  `WorkflowEnvironment` (no server), local dev is a single-binary `temporal server start-dev`, and
  production is self-host (docker-compose / Postgres-backed) or Temporal Cloud. "Lightweight" here
  means *you write workflow code, not a saga engine* — not zero infrastructure.

**Alternatives considered:**

| Engine | Verdict |
| --- | --- |
| **DBOS Transact** | Even lighter (a Postgres-only library, no server; reuses SwarmKit's Postgres backend). The strongest "ultra-light" option and a good *second* adapter; newer/less battle-tested than Temporal for multi-week human-in-the-loop, so not the first pick. |
| **Camunda / Zeebe (BPMN)** | Excellent saga + human-task semantics, but JVM + broker infra — heavier than warranted, and BPMN authoring competes with the StageGraph. |
| **n8n** | Great for lightweight event-glue and the *trigger* layer, weak for durable multi-week sagas with compensation. Candidate for the ingress/glue tier, not the saga engine. |
| **Prefect / Airflow** | Data-pipeline oriented; weaker human-in-the-loop-over-weeks story. |
| **Hand-rolled (status quo)** | Keep only as the zero-infra reference; do not harden into production. |

The seam makes the choice **reversible** — a DBOS or Camunda adapter can be added later without
touching the StageGraph, the stage runs, or the audit.

## The seam

Two halves: what an orchestrator *calls into SwarmKit* (stable), and what an orchestrator *implements*.

### 1. What SwarmKit exposes (the drive contract — stable, engine-agnostic)

The two seams named in `pipeline-controller.md`, made concrete as a small client any orchestrator uses:

- **`run_stage(requirement_id, stage) -> StageOutcome`** — kick the stage's topology as a bounded run
  over `swarmkit serve`, stamped with `correlation_id = requirement_id`. Returns the terminal outcome
  (`completed | parked-on-gate | rejected | denied | failed`). This is the slice-4 `StageRunner`
  behind a serve API.
- **`await_gate(requirement_id, gate)`** — the gate-resolution notification: SwarmKit signals the
  orchestrator when a funnel gate resolves (the pause itself is a SwarmKit checkpoint resumed by
  humans; the orchestrator only *learns* the result). Webhook/callback or poll of the gates API.

These are the *only* additions to the runtime — small, independently useful, and identical no matter
which engine sequences.

### 2. What an orchestrator implements (`OrchestrationProvider`)

A provider that, given a **published `StageGraph` spec** + a `requirement_id`, drives the saga:

```
start(requirement_id, graph, initial_event)   # begin a pipeline run
signal(requirement_id, event | gate_result)   # deliver an external event / gate resolution
state(requirement_id) -> SagaView              # live status (current stage, locks, pending gate)
cancel(requirement_id)                         # unwind with compensations
```

- **Reference adapter** — the slice-5 in-memory controller, wrapped to this interface.
- **Temporal adapter** — a **single, data-driven Temporal workflow** that *interprets* the StageGraph
  (topology-as-data preserved): stages → activities that call `run_stage`; `when`/`success` +
  external events + gate resolutions → **signals**; `locks` → a Temporal mutex-workflow (or Postgres
  advisory lock); `compensation` → the saga pattern; SLA/escalation → durable timers. One workflow
  implementation runs *any* StageGraph — the graph is not codegen'd into bespoke workflow code.

### The audit-correlation contract (what keeps the platform coherent)

Whichever engine sequences, **every stage run carries `requirement_id`**, so SwarmKit's append-only
audit assembles the full cross-stage trail (the DORA view) itself. This is the answer to *"if Temporal
orchestrates, is the audit split?"* — no: orchestration is delegated, **the governed record is not**.
Authoring stays in the composer (the StageGraph); the unified audit stays in SwarmKit; only the
durable sequencing runs on Temporal.

## What this changes on the roadmap

- The "harden the controller" path becomes **"define the `OrchestrationProvider` seam + ship the
  Temporal adapter,"** with the reference controller demoted to the zero-infra reference.
- The **pipeline editor** ([`pipeline-editor-canvas.md`](https://github.com/delivstat/swarmkit/blob/main/design/details/pipeline-editor-canvas.md)) is unaffected —
  it edits the `StageGraph` spec, which remains the source of truth the engine interprets.
- **Pipeline triggering** ([`pipeline-triggering.md`](pipeline-triggering.md)) feeds events to the
  seam's `signal()` — the same ingress works for the reference controller and Temporal.

## Non-goals

- **Not embedding Temporal in the core runtime.** The `temporalio` dependency lives in the adapter
  package/example, never in `swarmkit-runtime`. The runtime only gains the small stable drive seam.
- **Not re-authoring pipelines in the engine's format.** The StageGraph is the spec; the engine
  interprets it. No BPMN, no Temporal-workflow-as-the-source-of-truth.
- **Not mandating an engine.** Zero-infra deployments use the reference controller; the seam is the
  contract, Temporal is the recommended production implementation.

## Consequences

- **+** SwarmKit stops owning a distributed-systems problem it is not differentiated on; gains durable,
  battle-tested sequencing; keeps authoring + governance + audit in-platform.
- **+** Reversible engine choice; a second adapter (DBOS) validates the seam.
- **−** A production deployment adds a Temporal dependency (mitigated: reference controller for simple
  cases; Temporal's dev-server/test-env keep dev + CI infra-free).
- **−** Cross-run locking is not native to Temporal; the adapter implements it (mutex-workflow /
  advisory lock) — a known pattern, but adapter surface.

## Test plan

- **Seam conformance:** the reference adapter and the Temporal adapter pass one shared behavioural
  suite (start → advance → gate → compensate → cancel; dedup; out-of-order) — proving the seam is a
  real contract, not a shape.
- **Temporal adapter (in-process):** the StageGraph-interpreting workflow runs under
  `temporalio.testing.WorkflowEnvironment` (time-skipping, no server) — gate signals advance it,
  timers fire deterministically, compensation runs in reverse, locking serialises two requirements.
  Runs in CI with **no external infrastructure**.
- **Correlation:** every stage `run_stage` carries `requirement_id`; the assembled audit spans stages
  under both adapters.

## Demo plan

`just demo-pipeline-temporal`: the OMS pipeline driven by the Temporal adapter under the in-process
test environment — intake → design (signal-approved gate) → build → sit, a contended second
requirement serialising on the contract, a fired SLA timer escalating a stuck gate, and a cancellation
unwinding with compensations — the same scenarios the reference controller demo runs, now on durable
Temporal execution. Terminal transcript in the PR.
