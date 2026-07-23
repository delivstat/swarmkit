---
title: Pipeline triggering & ingress
description: How real-world events start and advance a pipeline — structured webhooks, an MCP tool, and NL/chat interpreted into a structured event — all delivered to the orchestrator's signal seam, with a governance guardrail for who may start or skip a stage.
tags: [pipeline, triggers, mcp, ingress, governance]
status: proposed
---

# Pipeline triggering & ingress

**Scope:** the *ingress* side of the pipeline — turning real-world events into the structured events
an orchestrator sequences on.
**Design references:** [`orchestration-provider-seam.md`](orchestration-provider-seam.md) (the
`signal()` seam this feeds), [`pipeline-controller.md`](pipeline-controller.md) (event model),
[`trigger` schema](../reference/trigger.md) (the mechanism to reuse).
**Status:** proposed.

## Why

Execution is event-driven: the orchestrator's entry point is `signal(requirement_id, event)` and the
saga advances to the stage whose `when` matches. But *nothing today turns a real webhook, an MCP call,
or a chat message into that structured event* — the orchestrator is driven in-process by the demo. This
note designs the **front door**: how CI/Jira/Git/SAST webhooks, agents/IDEs, and humans start and
advance a pipeline, and who is allowed to.

## Goal

An authorised event from the outside world becomes a structured
`InboundEvent(requirement_id, event, source_event_id, payload)` and reaches the orchestrator's
`signal()` — regardless of which orchestrator implementation is behind the seam.

## The three ingress paths

### 1. Structured webhook (CI / Jira / Git / SAST)

The source already speaks events. A receiver maps a signed payload → `InboundEvent` → `signal()`.
**Reuse the existing `Trigger` artifact** rather than a new mechanism: SwarmKit already has
`kind: Trigger` with `type: webhook`, HMAC signature validation, and a served endpoint — today its
`targets` fire a *topology*. Extend the target to a **pipeline event**:

```yaml
apiVersion: swarmkit/v1
kind: Trigger
metadata: { id: ci-build-ready }
type: webhook
targets:
  - pipeline: sdlc-pipeline          # the StageGraph
    emit: build.ready-in-qa          # the event to signal
    requirement_id: $.body.requirement_id   # extracted from the payload
```

The receiver validates the signature, extracts `requirement_id`, dedups on
`(requirement_id, emit, source_event_id)`, and calls `signal()`. Reuses the webhook auth + endpoint
that already exist.

### 2. MCP tool

For agents, IDEs, and bots: a governed MCP tool on the workspace's server —

```
submit_pipeline_event(pipeline, requirement_id, event, payload?)
```

— that validates the caller's scope and calls `signal()`. Clean, typed, and audited; any MCP client
(a chat integration included) can drive a pipeline through it.

### 3. Unstructured / natural language (a chat message)

A chat message — *"a new requirement RT-735 has been created, start analysis + design"* — is not a
structured event, so it has **two layers**:

1. **Interpret** — a small **router topology** (the Minder "LLM language, code doing" pattern:
   `feedback_llm_language_code_doing`) parses the message into
   `{requirement_id: "RT-735", event: "requirement.created"}`. Interpretation is itself a bounded,
   governed SwarmKit run.
2. **Emit** — the structured result is delivered via path 1 or 2 to `signal()`.

So: chat bot → router topology → `submit_pipeline_event` → orchestrator. Interpretation is SwarmKit's
job; sequencing is the orchestrator's; they meet at a structured event. A `Trigger` may wire this
end-to-end (a chat webhook whose target first runs the interpreter, then emits).

## Start, resume, and skip — one mechanism

Because the orchestrator routes an event to whatever stage's `when` matches, and the first event for a
`requirement_id` creates the saga, **`signal()` is simultaneously start, resume, out-of-order handling,
and skip:**

- **Start** — the first event (`requirement.created`) begins the pipeline at the entry stage.
- **Out-of-order** — an event routes to its stage whenever it arrives (dedup + reconciliation cover
  duplicates and drops).
- **Skip / start-mid-pipeline** — emitting a *later* stage's entry event (e.g. `design.kickoff` for a
  brand-new `RT-735`) starts at design; intake never runs. Stages are event-wired, not hard-dependency
  chained, so this is mechanically supported.

Two honest constraints on skip:

- **A skipped stage's *output* won't exist.** Skipping is safe only when the target stage does not read
  the skipped stage's artifact from the KB (or the operator seeds it).
- **A "start with the review, not the drafting" case** (a design already exists; run *only* the gate)
  is skipping *within* a stage. It is architecturally supported — the funnel takes an injectable
  drafter, so a pass-through drafter that returns the provided artifact gates it without re-drafting —
  but exposed as an explicit **seed-artifact-run-gate** ingress mode, not an accidental side effect.

## The governance guardrail (the load-bearing addition)

Starting or skipping a stage mid-pipeline is powerful and must not be an unauthenticated side effect
of any webhook. Ingress is therefore **policy-gated and audited**:

- **Scoped emission.** `signal()` requires a scope; a generic webhook may emit only the events a
  `Trigger` authorises (a CI trigger can emit `build.ready-in-qa`, not `design.approved`). An operator
  *starting or skipping* a stage manually (via MCP/CLI) needs a reserved scope
  (`pipeline:advance` / `pipeline:skip`) — a human-identity act, never grantable to an agent.
- **Audited.** Every ingress event is recorded on the append-only audit with its source
  (`webhook:ci-build-ready`, `mcp:alice`, `interpreter:RT-735`), so "who started RT-735 at design, and
  why" is answerable.
- **Idempotent.** Dedup on `(requirement_id, event, source_event_id)` at the seam, so a duplicated
  webhook or a retried MCP call never double-advances.

## Non-goals

- **Not the sequencing engine.** This is the front door; the saga behind it is
  [`orchestration-provider-seam.md`](orchestration-provider-seam.md).
- **Not per-vendor webhook code.** One signed-webhook receiver + a `Trigger` mapping; no Jira- or
  CI-specific handlers baked in.
- **Not free-form skip.** Skips are permissioned and audited, not an accidental capability.

## Test plan

- **Structured webhook:** a signed payload → the mapped `signal()` call; a bad signature is rejected; a
  duplicate `source_event_id` is a no-op.
- **MCP tool:** an in-scope call advances the saga; an out-of-scope call is denied and audited.
- **Interpretation:** a chat message routes through the interpreter topology to the correct structured
  event (mocked model).
- **Skip guardrail:** emitting a later stage's event without `pipeline:skip` is denied; with it, the
  saga starts at that stage and the skip is audited.
- **Seed-artifact-run-gate:** a provided design reaches the funnel gate without re-running the drafter.

## Demo plan

`just demo-pipeline-trigger`: (a) a signed CI webhook advances a running requirement; (b) a chat
message *"new requirement RT-735, start design"* is interpreted and starts the pipeline at design under
the `pipeline:skip` scope; (c) an unauthorised skip is denied and audited. Terminal transcript in the
PR.
