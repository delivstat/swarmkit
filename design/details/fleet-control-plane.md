---
title: Fleet control plane — cross-instance observability, eval, and human-gated self-improvement
description: A self-hostable control plane that aggregates OTel + audit + eval signals across many SwarmKit instances and turns them into human-approved self-improvement plans distributed back as data. Plus the eval harness and the ADK borrows that feed it.
tags: [serve, distributed, observability, governance, self-improvement, eval, otel, a2a]
status: proposal
---

# Fleet control plane

## Why this note

Two things motivate it:

1. **Multi-instance reality.** A per-instance `swarmkit serve` portal only ever sees
   its *own* traces. Across a fleet — many sites, many appliances (e.g. a Minder per
   home), many customer deployments — you want **emit-locally, aggregate-centrally**.
   A pattern that only shows up across fifty instances is invisible to any one of them.
2. **Turning observation into evolution.** Once telemetry + audit + eval are
   aggregated, they become the *evidence* for SwarmKit's third pillar —
   **growth-through-human-approved-authoring** (§12). The control plane is where the
   swarm *proposes* improvements from fleet-wide evidence and a human *approves* them.

This is the inter-instance / evolution layer. It sits **above**
[[distributed-architecture]] (which scales a *single* deployment horizontally —
worker pool + shared Postgres + a centralized OTel collector) and builds on
[[opentelemetry-observability]] (the telemetry foundation). It is NOT a rewrite of
either.

## What already exists (grounded)

- **OTel: mature.** `packages/runtime/src/swarmkit_runtime/telemetry/` — OTLP export,
  `swarmkit.*` semantic attributes (`topology.id`, `run.id`, `agent.id`,
  `tool.name`, governance decisions, approval waits), opt-in config. Resource
  attributes can already tag `service.instance.id`.
- **Audit: sealed + correlatable.** `audit/_provider.py` append-only `AuditProvider`
  (SQLite/mock), rich `AuditEvent` with `run_id` / `event_id` / `parent_event_id`,
  redaction (`audit/_redact.py`). Governance is uniform via `GovernanceProvider`
  (`governance/__init__.py`) — every model/tool/skill/agent action routes through it.
- **serve: real.** `server.py` — FastAPI, 15+ endpoints, job store, canary router,
  triggers, WS streaming. Per-instance.
- **Centralized OTel collector** is already in the [[distributed-architecture]] picture
  for one deployment.
- **Gaps (this proposal):** no **eval harness** (only per-output decision skills,
  `governance/_decision_evaluator.py`); no **cross-instance aggregation / control
  plane**; no **fleet self-improvement** (today: per-instance skill-gap logs →
  review queue → human authoring, §12.5). Today multi-instance aggregation is assumed
  to be the commercial Rynko layer — this note defines a **self-hostable OSS** path.

## The three planes

```
DATA PLANE — N swarmkit serve instances (+ edge single-instance, e.g. Minder)
  run swarms · enforce LOCAL governance gates · emit OTel + audit
  tagged: service.instance.id, workspace.id, deployment/tenant
  autonomous: keep running if the control plane is unreachable (buffer telemetry)
        │  OTLP + audit stream (signed, tagged)
        ▼
OBSERVABILITY PLANE — standard, do NOT reinvent
  OTel Collector → backend (Tempo/Jaeger + Grafana, or Rynko managed)
  raw spans/metrics, fleet-wide, via the OTel ecosystem
        │  swarm-semantic summary (not raw spans)
        ▼
CONTROL PLANE — the SwarmKit-specific part (new, OSS, self-hostable)
  ingest: aggregated audit (the §8 "media" branch at fleet scale) + eval results
          + skill-gap signals + drift scores + canary metrics, across instances
  surface: fleet run/trace inspection · eval dashboards · SELF-IMPROVEMENT PLANS
  approve: §8.7 reserved-scope human gates (skills:activate, topologies:modify)
  distribute: approved skills/topologies — as DATA — back to instances
```

### Why the split matters

- The **observability plane is commodity** — instances already export OTLP; lean on
  Collector + an existing backend for raw spans. SwarmKit must **not** build a trace
  database.
- The **control plane is the value** — it reads the *swarm-semantic* summary (which
  topology, which skill, which governance decision, eval scores, gaps), not a billion
  raw spans, and it runs the self-improvement loop.

## The self-improvement loop, at fleet scale

**Observe → Measure → Propose → Approve → Grow** — pillar #3 made operational:

1. **Observe** — aggregated traces + audit show *where* swarms fall back, escalate to
   HITL, or fail, across the fleet.
2. **Measure** — the **eval harness** (below) scores topology behaviour; regressions
   and low scores across instances become signals.
3. **Propose** — the control plane mines those signals into **self-improvement plans**:
   "add a `hairnet` skill (12 instances hit this gap)", "this route falls back 40% on
   instances running model X — retune", "topology Y regressed after skill Z v3".
   Proposal only — never auto-applied (§12.5).
4. **Approve** — a plan touching `skills:activate` / `topologies:modify` lands in the
   **human approval queue**; those scopes are reserved for human identity (§8.7),
   unchanged. The control plane is *where* the human acts; the policy is the same.
5. **Grow** — an approved change is **data** (a skill file, a topology edit) →
   versioned, signed, and **distributed** to instances (pull: instances poll for
   approved artifact versions; provenance verified via the existing
   `skill.metadata.provenance`). This is [[installable-expertise-packages]] /
   skills-library distribution realized at fleet scale.

**The moat:** because a swarm change is *data*, fleet-wide self-improvement is a
**data-distribution problem, not a redeploy**. A code-first framework (ADK) must ship
new code to every node; SwarmKit pushes approved YAML. That is only possible because of
pillar #1 (topology-as-data) — and the central aggregation is what makes "detect the
gap across the fleet" possible in the first place.

## Borrowed from ADK (folded in)

ADK's build→eval→observe→deploy loop maps onto this; the borrows that sharpen
SwarmKit's own thesis:

1. **Eval harness (the #1 borrow; genuine gap).** Eval sets as **data artifacts**
   (`input → expected trajectory + expected response`), a `swarmkit eval` command, and
   scoring that **reuses the existing decision-skill judges**
   (`governance/_decision_evaluator.py`, Tier 2/3). Results feed the control plane's
   "Measure" step and gate the §12 "test" step (gap → author → **test** → publish).
   Independently useful per-instance too.
2. **Deterministic workflow primitives** — Sequential / Parallel / Loop as named
   **topology archetypes** (the compiler already builds DAGs; these are blessed
   patterns). Reinforces topology-as-data + "LLM does language, code does the doing".
3. **Agent-as-skill / A2A interop** — model a remote A2A agent, a sub-swarm, or
   *another instance's* swarm as a **coordination skill** (keeps "skills are the only
   extension primitive"). Builds on the existing A2A adapter (`_delegation.py`) +
   §18; also enables cross-instance delegation.
4. **Uniform interception surface** — SwarmKit *already* intercepts uniformly via
   `GovernanceProvider` + OTel spans at every model/tool/skill/agent call (more
   principled than ADK's ad-hoc callbacks). The only borrow: ensure **eval +
   self-improvement signals hang off the same contract**, not a side channel.
5. **Trace inspection** — per-instance in the existing serve/UI (edge/dev, Minder);
   fleet-wide in the control-plane cockpit.

Explicitly **not** borrowed: managed GCP/Vertex deploy (collides with invariant #4);
Gemini-first defaults (ModelProvider abstraction already covers multi-provider).

## Reconciliation with SwarmKit's principles

- **Invariant #4 (no lock-in):** the control plane is **OSS + self-hostable**,
  OTel-standard; **Rynko is an optional managed backend, never required**. This is the
  open-source answer to "Rynko is the aggregation layer."
- **No central SPOF for execution:** the control plane is for evolution/observation
  only. Instances run fully with it down — buffer telemetry, apply no new approved
  changes. It is never a runtime dependency.
- **Invariant #7 (eject intact):** additive observability/evolution; an ejected swarm
  still runs standalone. The control plane never participates in execution.
- **§8 governance unchanged:** approval flows through the same reserved-scope human
  gates; audit stays append-only; distribution artifacts are signed + provenance-
  verified.
- **§12.5 (no autonomous self-modification):** the plane **proposes**; humans
  **approve**; only then distribute. Fleet aggregation changes the *evidence*, not the
  *consent model*.
- **§9 components:** adds an **optional fourth component** —
  `swarmkit-control-plane` — beside runtime / UI / schema. The existing per-instance
  UI dashboard is the *single-instance* surface; the control plane is the *fleet*
  surface. Same UI codebase can point at either.
- **§15.3 / invariant #8:** the control-plane cockpit is an **operational + governance
  surface** (observe / measure / review-and-approve), distinct from the deferred
  **composer/authoring UI**. It shows plans and captures approval; the authoring
  mechanics stay conversational/CLI. (One-line clarification to add to §15.3.)

## Phased roadmap (additive; each its own design note + PRs)

1. **Eval harness** (standalone value, no fleet needed) — eval-set schema, `swarmkit
   eval`, decision-skill scoring, result storage. *Unlocks the "test" gate + the
   "Measure" signal.*
2. **Fleet aggregation** — instance/tenant resource tagging (mostly config), a
   documented OTel Collector + backend deployment, and a **semantic-summary ingest**
   (audit + eval + gaps) into a control-plane store. *Read-only fleet observability.*
3. **Self-improvement cockpit** — fleet trace/eval views + the plan generator
   (gap-mining → proposals) + the approval queue (existing reserved scopes) + signed
   artifact distribution (pull). *The loop closes.*
4. **Parallel small items** — workflow archetypes (Seq/Par/Loop); A2A-agent /
   cross-instance-swarm as a coordination skill.

## Open questions

1. **Distribution: pull vs push.** Pull (instances poll for approved artifact
   versions) is simpler + firewall-friendly for edge appliances; push needs
   reachability. Default **pull**.
2. **Control-plane store.** Reuse the Supabase/Postgres direction from
   [[distributed-architecture]] for the semantic store, or keep it separate?
3. **Tenant isolation** in aggregation (multi-customer): resource-attribute scoping +
   per-tenant approval queues.
4. **Edge appliances (Minder):** opt-in fleet enrollment; a homeowner's box probably
   *doesn't* join a fleet, but a fleet operator (a security company running many) would.
   Enrollment + consent model.
5. **OSS vs Rynko boundary:** what's in the OSS control plane vs the Rynko managed
   layer (this note: OSS = the full loop, self-hostable; Rynko = managed + scale).

## Test / demo plan

- **Eval harness:** an eval set against a reference topology scores trajectory +
  response; a regression run flags a drop. Standalone, unit-testable.
- **Aggregation:** two `swarmkit serve` instances → Collector → control-plane ingest
  shows both instances' runs in one fleet view, correctly tagged.
- **Self-improvement:** seed a repeated gap across instances → the plan generator
  proposes a skill → it lands in the approval queue (human-gated) → approve →
  the new skill distributes and a target instance picks it up. End-to-end, governed.
- **Degradation:** kill the control plane → instances keep running + buffer telemetry;
  no new approvals apply; recovery drains the buffer.
