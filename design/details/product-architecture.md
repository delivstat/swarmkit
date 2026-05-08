---
title: Product architecture
description: Deployment models, open-source vs commercial split, and UI as the paid product layer.
tags: [product, architecture, deployment]
status: draft
---

# Product architecture

**Scope:** cross-cutting (runtime, UI, cloud)
**Design reference:** §9 (three-component system), §15 (UI), §20 (phasing)
**Status:** draft

## Goal

Define the deployment architecture and open-source/commercial boundary for SwarmKit as a product, with the Rynko platform (rynko.dev) as the commercial host.

## Core principle

The runtime is open-source. The UI is a commercial product hosted on the Rynko platform. The CLI is the free, complete interface.

## Brand separation

- **SwarmKit** = the open-source framework. Own GitHub org, own docs, own identity. No Rynko branding in the repo, CLI, or runtime.
- **Rynko** (rynko.dev) = the commercial platform that hosts SwarmKit's operational UI. The SwarmKit dashboard is a product surface within Rynko, alongside existing capabilities (flows, extraction, validation gates).

The relationship is analogous to Kubernetes (open-source orchestrator) and GKE/EKS (commercial platform you run it on). SwarmKit users who want a visual ops layer sign up for Rynko — one account, one billing relationship, one place to manage agents, flows, and gates.

Rynko's existing infrastructure — auth, teams, billing, flow/gate pipeline — is reused rather than rebuilt. SwarmKit agent topologies and Rynko validation flows are complementary: an agent can trigger a Rynko gate, a Rynko flow can invoke a SwarmKit topology.

## Open-source boundary

The open-source project includes:

- **Runtime** — topology interpreter, LangGraph compiler, governance engine, model providers, skill registry, CLI
- **Schema** — canonical JSON schemas, Python and TypeScript validators
- **CLI** — full operational surface: `swarmkit run`, `swarmkit status`, `swarmkit logs`, `swarmkit audit`, `swarmkit ask`, approval prompts, drift scores
- **Authoring swarms** — conversational topology and skill authoring via CLI chat

The open-source project is complete and self-sufficient. A solo developer or team can build, run, debug, and evolve swarms entirely from the terminal with no paid dependency.

## Commercial product: SwarmKit on Rynko

The UI (dashboard, topology composer, runtime monitor, approval queue) is a paid product surface on the Rynko platform. It is not open-source.

### Why the UI is commercial

- The runtime must run in the user's environment (security, data sovereignty, credential access). It cannot be a hosted service. This limits SaaS revenue on the runtime itself.
- The UI is where operational, team, and compliance value concentrates — visibility, collaboration, analytics, alerting, audit retention. These justify recurring revenue.
- Rynko already has the platform infrastructure (auth, teams, billing, validation gates) — the SwarmKit UI is a natural extension, not a greenfield build.
- Precedent: Datadog (agent open, UI closed), Grafana (core open, enterprise UI features paid), GitLab (core open, premium tiers for ops/compliance).

### What the UI provides over the CLI

| Capability | CLI (free) | UI (paid) |
|-----------|-----------|-----------|
| Topology authoring | YAML + authoring swarms | Visual composer with structure/relationships/network views |
| Run monitoring | `swarmkit status`, `swarmkit logs` | Real-time dashboard, step-by-step agent trace visualization |
| Approval gates | Terminal prompts | Visual review queue with context, history, and team assignment |
| Audit log | `swarmkit audit` (local, ephemeral) | Managed, searchable, exportable, retained per policy |
| Intent drift | CLI output per run | Visualized trends, cross-run analytics, learned thresholds |
| Team features | None | RBAC, SSO, workspace sharing, "who approved what" |
| Alerting | None | Slack/email/webhook on drift, failures, approval requests |
| Skill catalogue | `swarmkit skills list` (local) | Browse and install community/verified skills |
| Compliance | DIY | Audit export for SOC2/ISO, retention policies, tamper-proof storage |

## Deployment models

### 1. Open-source (CLI only)

Everything runs locally. No UI server, no cloud dependency.

```
User's environment
├── swarmkit runtime (Python)
├── local SQLite/files for audit log
└── CLI for all operations
```

**Audience:** solo developers, experimentation, open-source community.

### 2. Cloud-hosted UI on Rynko (primary commercial model)

The runtime runs in the user's environment. It pushes structured telemetry to the Rynko platform. The UI is hosted on rynko.dev.

```
User's environment                    Rynko platform (rynko.dev)
├── swarmkit runtime ──telemetry──▶  ├── event ingestion API
│   (agents, tools,                  ├── managed database
│    MCP servers,                    ├── analytics engine
│    credentials)                    ├── alerting service
│                                    ├── existing flows/gates
│                                    └── SwarmKit dashboard
└── local execution only                  │
                                          ▼
                                    rynko.dev/swarmkit
```

**What the runtime sends:**
- Audit events (agent steps, governance decisions, approval outcomes)
- Drift scores
- Run metadata (topology ID, agent IDs, timing, success/failure)
- Skill gap detections

**What the runtime never sends:**
- User credentials or API keys
- Raw LLM prompts/responses (unless user opts in for debugging)
- MCP server traffic
- Local file contents

**Runtime configuration:**

```yaml
# ~/.swarmkit/config.yaml
platform:
  mode: cloud
  endpoint: https://api.rynko.dev
  api_key: rk-...
  telemetry:
    send_audit_events: true
    send_drift_scores: true
    send_prompts: false
```

**Audience:** teams, production deployments, anyone who wants visual ops without running infrastructure.

### 3. Self-hosted UI (enterprise)

Same UI, deployed in the customer's environment. Connects to a customer-managed database. No data leaves their network.

```
Customer's environment
├── swarmkit runtime
├── swarmkit UI server (Docker/Helm)
├── customer-managed Postgres
└── all data stays internal
```

**Audience:** enterprises with strict data policies, regulated industries, air-gapped environments.
**Pricing:** annual contract, includes support and onboarding.

## Upgrade path

The transition from CLI to Rynko is frictionless:

1. User is running swarms via CLI (free, open-source)
2. User signs up for rynko.dev, gets an API key
3. User adds the API key to `~/.swarmkit/config.yaml`
4. Existing runtime starts sending telemetry to Rynko
5. All current and future runs appear in the dashboard alongside any existing Rynko flows/gates

No migration, no re-architecture, no data export/import. The runtime doesn't care where it sends events.

## Revenue model

| Tier | What you get | Price |
|------|-------------|-------|
| **Open-source** | Runtime + CLI, full functionality, unlimited agents/runs | Free |
| **Team** | Rynko cloud UI, team RBAC, audit retention, alerting, drift analytics | Per-seat/month or usage-based |
| **Enterprise** | Self-hosted UI, SSO/SAML, compliance exports, SLA, support | Annual contract |

The free tier is generous enough that solo developers never need to pay. Conversion happens naturally when teams need collaboration, visibility across environments, retention, and compliance — capabilities that don't make sense to build yourself.

## Non-goals

- Running the runtime in Rynko's cloud — agents must execute in the user's environment
- Crippling the open-source CLI to force UI adoption — the CLI is a complete product
- Open-sourcing the UI — the UI is the commercial differentiator

## Open questions

- Exact telemetry protocol — WebSocket, gRPC, or HTTPS polling?
- Data retention defaults per tier
- Whether the enterprise self-hosted tier includes the analytics/learning engine or just the dashboard
- Pricing model details (per-seat vs per-run vs hybrid)
- How `threshold: auto` learned profiles work across cloud vs self-hosted (see `intent-drift-detection.md`)
- How SwarmKit topologies and Rynko flows/gates integrate at the product level — shared workspace? unified run history?
- Branding in the UI — "SwarmKit on Rynko" vs just a section within the Rynko dashboard
