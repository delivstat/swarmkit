---
title: Product architecture refinements
description: Architectural refinements from external feedback on the product architecture, OTel, and intent drift design notes.
tags: [product, architecture, runtime, observability]
status: draft
---

# Product architecture refinements

**Scope:** cross-cutting (runtime, observability, product)
**Design reference:** `product-architecture.md`, `opentelemetry-observability.md`, `intent-drift-detection.md`
**Status:** draft

## Context

Consolidated feedback on the product architecture (#87), OpenTelemetry observability (#88), and intent drift detection (#86) design notes. These refinements should be folded into the respective design notes as they move to implementation.

## 1. Defer self-hosted UI until revenue justifies it

The enterprise self-hosted UI (Docker/Helm) is listed in `product-architecture.md` as a deployment model. Supporting on-prem software across custom Kubernetes clusters, varied ingress controllers, and restrictive enterprise firewalls is a massive engineering drain that turns product teams into outsourced IT support.

**Decision:** treat self-hosted UI as Phase 3 — only when revenue from cloud-hosted tier justifies dedicated deployment engineering. Push the cloud-hosted UI + local runtime (Tier 2) as the primary enterprise pitch. Self-hosted is a negotiation lever for large contracts, not an actively supported product in the early stages.

## 2. Local ring buffer for prompt/response debugging ("Privacy-First Debugger")

The runtime sends only structural telemetry (OTel traces) to Rynko. But the primary reason a developer opens the dashboard is to figure out *why* an agent failed — and that requires prompt/response payloads.

**Solution:** a local ring buffer of prompt/response pairs, keyed by OTel span ID. Prompts never leave the user's environment.

### Design

- **Storage:** local SQLite database (not in-memory). Must survive process restarts — overnight batch jobs fail, developer debugs the next morning.
- **Keyed by:** OTel span ID, linking local debug data to cloud trace visualization.
- **Retention:** configurable. Default: last 7 days or last N runs, whichever is larger.
- **Access:**

```bash
swarmkit debug --span-id abc123    # prompt/response for a specific span
swarmkit debug --run-id xyz        # all prompts for a run
swarmkit debug --agent researcher --last 5  # last 5 steps for an agent
```

- **Privacy guarantee:** the ring buffer is local-only. Rynko never receives prompt content unless `send_prompts: true` is explicitly set.

### Marketing angle

This enables a "Zero-Trust AI Ops" positioning — enterprises get a collaborative debugging UI without proprietary data ever leaving their VPC. The Rynko dashboard shows the structural trace; the CLI pulls the sensitive content from local storage when needed.

## 3. LangGraph checkpointer for approval gate state persistence

When an agent hits a Rynko approval gate, execution may pause for hours or days. The runtime process may terminate during that wait.

**Solution:** leverage LangGraph's built-in checkpointer to serialize and rehydrate graph state.

### Flow

1. Agent hits approval gate
2. Runtime serializes full graph state via LangGraph checkpointer (SQLite locally, Postgres for production)
3. Runtime process can safely terminate — state is durable on disk
4. Approval arrives (Rynko webhook or CLI input) hours/days later
5. Runtime rehydrates from checkpoint, resumes from exactly where it paused

### Implementation note

Approval gates should compile to LangGraph interrupt points. The `langgraph-compiler.md` design note covers the compilation target but has not explicitly addressed checkpointing for long-lived pauses — this is a gap to close.

SwarmKit does not manage state freezing manually. LangGraph already solved durable execution.

## 4. OTLP/HTTP as the starting transport

OTLP supports both gRPC and HTTP. Start with OTLP/HTTP using asynchronous batching.

**Why HTTP first:**
- Keeps the runtime lightweight
- Avoids connection-drop headaches of gRPC in diverse network environments (enterprise firewalls, proxies)
- Makes Rynko instantly compatible with the broader observability ecosystem
- Move to gRPC only when payload size or velocity becomes a bottleneck

This refines the open question in `opentelemetry-observability.md`.

## 5. Usage-based pricing over per-seat

Per-seat pricing misaligns with agentic workloads. A swarm may run 10,000 times a day autonomously without a human logging into the UI — generating massive telemetry costs with zero "seat" revenue.

**Direction:** per-run or per-agent-step pricing with a generous free tier. Monetize only when users need the cloud ops surface. The free tier covers local execution with the CLI.

This refines the pricing open question in `product-architecture.md`.

## 6. Unified workspace — SwarmKit + Rynko flows

When a SwarmKit agent triggers a Rynko validation gate, the user should be able to click a trace link and see the exact topology run that triggered it. And from the SwarmKit trace, they should see the Rynko gate result.

**Requirement:** shared workspace, unified run history, one timeline. This is the biggest moat — no other tool shows agent orchestration and data validation in a single view.

This refines the integration open question in `product-architecture.md`.

## Consolidated storage architecture

| Layer | What | Storage | Leaves user env? |
|-------|------|---------|-------------------|
| Execution state | LangGraph checkpointer | Local SQLite/Postgres | No |
| Prompt/response debug | Local ring buffer | Local SQLite, keyed by span ID | No (unless opted in) |
| Structural telemetry | OTel traces + metrics | Rynko cloud (or any OTLP backend) | Yes — structural only |
| Governance audit log | Append-only event log | Local + replicated to Rynko | Yes — decisions only |
| Learned drift profiles | Historical drift scores | Rynko cloud (or local for CLI-only) | Yes — scores only |

Each layer has its own retention, privacy boundary, and persistence guarantee.

## Open questions from feedback

- How granular should the prompt opt-in be? Per-topology? Per-agent? Per-run? Per-MCP-server?
- Should the local ring buffer support a "secure tunnel" mode where the Rynko UI can pull prompts on-demand from the runtime (with user approval), or is CLI-only access sufficient?
- How does the checkpointer interact with DAG topologies where multiple agents may be paused at different approval gates simultaneously?
