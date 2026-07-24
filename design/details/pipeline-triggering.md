---
title: Pipeline triggering & ingress
description: How real-world events start and advance a pipeline — structured webhooks, an MCP tool, and NL/chat interpreted into a structured event — all delivered to the orchestrator's signal seam, with a governance guardrail for who may start or skip a stage.
tags: [pipeline, triggers, mcp, ingress, governance]
status: partially-implemented
---

# Pipeline triggering & ingress

**Scope:** the *ingress* side of the pipeline — turning real-world events into the structured events
an orchestrator sequences on.
**Design references:** [`orchestration-provider-seam.md`](orchestration-provider-seam.md) (the
`signal()` seam this feeds), [`pipeline-controller.md`](pipeline-controller.md) (event model),
[`trigger` schema](../../packages/schema/schemas/trigger.schema.json) (the mechanism to reuse).
**Status:** partially-implemented.

## Implementation status

- **Trigger pipeline-event target — shipped (schema).** A `Trigger`'s `targets` items are now a union:
  each item is either a topology id (fires that topology, unchanged) or a **pipeline-event target**
  `{ pipeline, emit, correlation_id? }` that signals a StageGraph — the "Structured webhook" path
  above. Landed in `packages/schema/schemas/trigger.schema.json` (schema 1.18.0 / TS 0.8.0), with valid
  + invalid fixtures. Domain-neutral: the extraction key is `correlation_id` (an opaque handle), **not**
  a business-specific id — the illustrative `requirement_id` in the YAML below predates that decision.
- **Ingress front door + governance guardrail — shipped (runtime, 1.102.0).** `POST /pipelines/signal`
  turns an authorised outside event into a structured `(correlation_id, event)` and hands it to an
  **injected** signal sink (`app.state.pipeline_signal`, a `PipelineSignal = (correlation_id, event) ->
  None` exported from `swarmkit_runtime.orchestration` — the runtime owns no orchestrator, exactly like
  the `RunStage` run-stage seam). Body: `{ correlation_id, event, source_event_id?, mode }` with
  `mode ∈ {emit, advance, skip}`. The guardrail (`_ingress_pipeline_event`, shared verbatim by the
  endpoint and the MCP tool) is **authorize → audit → deliver**: `advance` / `skip` are operator acts
  that require the caller's identity to hold the reserved scope `pipeline:advance` / `pipeline:skip`
  through the `GovernanceProvider` (`evaluate_action`) — a denied authorization is a 403; `emit` needs
  only the serve `run` tier. Every attempt (allowed *or* denied) is recorded append-only on the audit,
  stamped with the source (`api:<client>` / `mcp:<pipeline>`) and `(correlation_id, event, mode)`, with
  `source_event_id` passed through for the orchestrator's dedup — the runtime keeps **no** dedup state.
  Delivery is a sanctioned **503** when the sink is unset.
- **Reserved scopes — shipped.** `pipeline:advance` and `pipeline:skip` are in `auth/_scopes.py`
  `RESERVED_SCOPES`, so a transport (api-key / JWT) token can never carry them (`reserved_violations`
  rejects them at token load) — starting or skipping a stage is structurally un-grantable to an
  agent/webhook token (CLAUDE.md invariant #6 / design §8.7).
- **MCP tool — shipped.** `submit_pipeline_event(pipeline, correlation_id, event, mode="emit")` on the
  workspace MCP server routes through the *same* `_ingress_pipeline_event` guardrail; the MCP caller is
  a transport principal (`mcp`) that holds neither reserved scope, so an agent may `emit` but never
  `advance` / `skip` on its own authority.
- **Webhook → pipeline receiver — shipped (runtime, 1.103.0).** `POST /hooks/{trigger_id}` now routes
  to the pipeline ingress when the named `Trigger` targets a `pipeline_target` (resolved by trigger id;
  a topology-id webhook keeps its existing job-start behaviour, back-compat). The receiver validates the
  trigger's HMAC signature (`_check_pipeline_webhook_signature`, reusing `validate_webhook_signature`),
  extracts the opaque `correlation_id` from the JSON body via the target's dotted `$.a.b.c` path
  (`extract_correlation_id` in `triggers/_pipeline_ingress.py` — a tiny resolver, no external jsonpath
  dep), and calls the *same* `_ingress_pipeline_event` guardrail with `mode="emit"`, `source=
  webhook:{trigger_id}`. **Scoped emission is structural:** the receiver only ever emits the trigger's
  *declared* `emit` event(s); a body that asks for a different `event` or a non-`emit` `mode` is a 403 —
  a webhook can never advance/skip a stage (those stay reserved human-identity operator acts). The
  resolver now carries pipeline targets through (`ResolvedTrigger.pipeline_targets`) instead of
  mis-checking them as topology ids. Demo: `just demo-pipeline-trigger`.
- **Not yet (later PR):** the NL/chat interpreter router topology (path 3 above) — a chat message parsed
  into a structured event, then emitted via the ingress front door that now exists end-to-end.

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
