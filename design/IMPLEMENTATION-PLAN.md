---
title: Implementation Plan — SwarmKit
description: Phased roadmap from foundation through production readiness. Incorporates product architecture, observability, intent drift, and ecosystem features.
tags: [plan, milestones, roadmap]
status: active
---

# Implementation Plan — SwarmKit

**Source of truth:** `design/SwarmKit-Design-v0.6.md` (§20.1 lists the original Phase 1 scope). This plan extends that scope with features from design notes landed since v1.0.0. Every feature becomes one or more PRs under the [feature delivery workflow](../CLAUDE.md#feature-delivery-workflow--mandatory).

**Status:** originally drafted 2026-04-21. Reorganised 2026-05-08 to incorporate product architecture (`product-architecture.md`), OpenTelemetry observability (`opentelemetry-observability.md`), intent drift detection (`intent-drift-detection.md`), market analysis (`market-analysis-and-risk-mitigations.md`), and ecosystem features.

**Scope:** this plan covers the **open-source SwarmKit framework** only. The commercial Rynko platform (UI, cloud telemetry, team features) has its own plan — see `design/details/product-architecture.md` for the boundary.

## How this plan works

- **Phases** group milestones by theme and priority. Phase 1 is complete. Phase 2 is current priority.
- **Milestones** are coarse checkpoints. Each has an **exit demo** — something a human can watch.
- One feature = one design note at `design/details/<slug>.md` + one implementation PR.
- Milestones are mostly sequential but features within a milestone often parallelise.

## Phase and milestone overview

| Phase | # | Milestone | Status | Exit demo |
|-------|---|-----------|--------|-----------|
| 1 | M0 | Schemas | ✅ | `just demo-schema` validates all fixtures in Python + TS |
| 1 | M1 | Topology loading & resolution | ✅ | `swarmkit validate` prints resolved tree |
| 1 | M2 | GovernanceProvider + AGT Tier 1 | 🟡 | AGT policy denies + audits; CLI wiring gap remains |
| 1 | M2.5 | ModelProvider abstraction | ✅ | Multi-provider topology loads and runs |
| 1 | M3 | LangGraph compiler | ✅ | `swarmkit run` executes two-agent swarm |
| 1 | M3.5 | Conversational authoring (v1) | ✅ | `swarmkit init` produces working workspace |
| 1 | M4 | Decision + persistence skills | ✅ | Structured output + LLM judge + review queue |
| 1 | — | DAG dependency graph | ✅ | Agents execute in dependency order |
| 2 | M5 | MCP integration (finish) | 🟡 | MCP calls gated through governance |
| 2 | M6 | Observability + human interaction | — | `swarmkit status/logs/review/ask` + OTel traces |
| 2 | M7 | Intent drift detection | — | Drift scores per step, nudge on threshold breach |
| 3 | M8 | Knowledge + skills ecosystem (enhance) | 🟡 | Skill registry CLI + user knowledge server + knowledge curator topology |
| 3 | M9 | Reference topologies (enhance) | 🟡 | Code review + skill authoring swarms runnable end-to-end |
| 4 | M10 | Eject + execution modes | — | `swarmkit eject` + `swarmkit serve` + canary deployments |
| 4 | M11 | Launch prep | — | `pip install swarmkit` → working swarm in <15 min |

## Cross-cutting workstreams

Run in parallel with all milestones:

- **CI: ✅ DONE.** GitHub Actions: lint + typecheck + test (py 3.11/3.12/3.13 + JS + schema codegen drift + JSON Schema validity). `design/details/ci-pipeline.md`, PR #2.
- **Docs.** Concept pages land with their milestone. Machine migration + local LLM setup guide landed (2026-04-25).
- **LLM-friendly knowledge.** `llms.txt` current, frontmatter on design notes, error messages readable-as-docs, usability-first review per PR. See `docs/notes/llm-friendly-knowledge.md` and `docs/notes/usability-first.md`.
- **Governance hardening.** Every milestone touching `governance/` is reviewed against §8 Separation of Powers invariants.
- **Schema hosting.** JSON Schemas need `$id` URLs under `schemas.swarmkit.dev`. GitHub Pages path. Blocking for public launch (M11).
- **Packaging.** PyPI + npm + Docker publish workflows finalised in M11. Trial runs from M5 onward.

---

## Phase 1 — Foundation (COMPLETE)

All milestones in this phase shipped between 2026-04-21 and 2026-04-26. v1.0.0 tagged 2026-04-26. Preserved here as historical record.

### M0 — Schemas ✅

**Goal:** every artifact example validates in both Python and TS. Codegen Pydantic models + TS types.

**Design reference:** §6.3, §10, §13, §9.3.

**Features:**

- [x] `topology-schema-v1.md` — PR #5
- [x] `skill-schema-v1.md` — PR #8
- [x] `archetype-schema-v1.md` — PR #9
- [x] `workspace-schema-v1.md` — PR #10
- [x] `trigger-schema-v1.md` — PR #11
- [x] Pydantic model codegen — PR #12
- [x] TypeScript type codegen — PR #13
- [x] Round-trip tests: 182 Python / 108 TS

**Exit demo:** `just demo-schema` — green validation report across all fixtures and languages.

### M1 — Topology loading & resolution ✅

**Goal:** load and resolve every topology, archetype, and skill file in a workspace.

**Design reference:** §10, §14.3.

**Features:**

- [x] Workspace directory loader — PR #18
- [x] Archetype + skill resolvers — PRs #20, #21
- [x] ResolvedTopology data model — PR #21
- [x] `swarmkit validate` with human-readable errors — PR #23
- [x] Hello-swarm on-ramp + demo-resolver — PR #23
- [x] `swarmkit knowledge-pack` CLI — PR #23
- [ ] Resolve every `reference/` artifact — gated on reference topologies landing (M9)

**Exit demo:** `just demo-resolver` — valid workspace resolves; broken workspace prints actionable error.

### M2 — GovernanceProvider + AGT Tier 1 🟡

**Goal:** governance abstraction with real AGT policy engine for Tier 1 checks.

**Design reference:** §8.5, §8.6, §16.2, §16.3.

**Features:**

- [x] `governance-provider-interface.md` — interface stabilised
- [x] AGTGovernanceProvider (policy + audit + identity) — 194 lines, 10 integration tests
- [x] MockGovernanceProvider — used in all unit tests
- [x] Middleware pipeline for skill invocation — PR #43
- [x] Separation-of-powers integration tests
- [ ] **CLI governance provider wiring** — `swarmkit run` reads `workspace.yaml` `governance:` block instead of hardcoding mock. Shared gap with M5.

**Exit demo:** AGT denies unauthorised scope, audit records denial with tamper-evident hash chain.

### M2.5 — ModelProvider abstraction ✅

**Goal:** per-agent LLM provider selection via topology YAML.

**Features:**

- [x] ModelProvider ABC + 7 built-in providers (Anthropic, OpenAI, Google, Ollama, OpenRouter, Groq, Together)
- [x] Provider registry + env-var discovery
- [x] `SWARMKIT_PROVIDER` / `SWARMKIT_MODEL` overrides

**Exit demo:** `swarmkit run` dispatches to whichever provider has credentials.

### M3 — LangGraph compiler ✅

**Goal:** topology → StateGraph with delegation, skill dispatch, checkpointing.

**Design reference:** §14.3, §14.5, §5.3. `design/details/langgraph-compiler.md`.

**Features:**

- [x] Node + edge construction from agent hierarchy — PR #35
- [x] Delegation via `delegate_to_<child>` tool calls
- [x] Capability + coordination skill dispatch
- [x] SQLite checkpointer wiring
- [x] `swarmkit run` one-shot execution
- [x] Long-lived pause support via LangGraph interrupt points (approval gates)

**Exit demo:** `swarmkit run examples/hello-swarm/workspace hello` — two-agent delegation and synthesis.

### M3.5 — Conversational authoring (v1) ✅

**Goal:** users describe swarms in natural language; never write YAML.

**Design reference:** §11, §12, §14.2. `design/details/conversational-authoring.md`.

**Features:**

- [x] Authoring agent loop + tools (validate_yaml, write_files, etc.) — PR #37
- [x] `swarmkit init` — interactive workspace creation
- [x] `swarmkit author topology/skill/archetype` — artifact authoring
- [x] `swarmkit author mcp-server` — MCP server authoring (M5)

**Exit demo:** `swarmkit init` → working workspace → `swarmkit validate` passes → `swarmkit run` produces output.

### M4 — Decision + persistence skills ✅

**Goal:** LLM judges, deterministic validators, audit writes, review queue, skill gap log.

**Design reference:** §6.2, §8.6, §12.1, §14.5, §17.

**Features:**

- [x] Structured output governance + auto-correction — PRs #38, #39
- [x] LLM-judge primitive skill — PR #43
- [x] Schema-validator primitive skill — PR #38
- [x] Multi-persona panel composition (Tier 3) — PR #43
- [x] Review queue + skill gap log — PR #41
- [x] Inline HITL + `swarmkit review` / `swarmkit gaps` — PR #42
- [x] AGT trust scoring integration — PR #44

**Exit demo:** structured output + auto-correction + decision skills + review queue all working.

### DAG dependency graph ✅

**Goal:** agents declare `depends_on` for parallel-with-dependencies execution.

**Design reference:** `design/details/dag-dependency-graph.md`.

**Features:**

- [x] Schema extension: `depends_on` on child agents — PR #83
- [x] DAG validation (cycle detection, reference validation)
- [x] DAG router + dependency-based execution — PR #83
- [x] E2E tests — PR #84

---

## Phase 2 — Runtime Completion (CURRENT PRIORITY)

Finish the runtime to production quality. Everything here is open-source. These milestones make SwarmKit ready for real workloads.

### M5 — MCP integration (finish) 🟡

**Goal:** real MCP servers power capability skills; governance gates every MCP call.

**Design reference:** §18. `design/details/mcp-client.md`.

**Status:** ~70% complete. Core plumbing done. Remaining: governance wiring, sandboxing, reference skills.

**Features (done):**

- [x] MCPClientManager + stdio/SSE transports — PR #45
- [x] MCP server registry in workspace.yaml — PR #47, fixed PR #49
- [x] Schema↔runtime alignment + hello-world example — PR #49
- [x] `swarmkit author mcp-server` — conversational authoring
- [x] Knowledge Curator topology design — PR #46
- [x] Skill registry design — `design/details/skill-registry.md`

**Features (remaining):**

- [ ] **CLI governance provider wiring** — `swarmkit run` reads `workspace.yaml` `governance:` block. Closes M2 gap too.
- [ ] MCP calls gated through GovernanceProvider — `evaluate_action` before `call_tool`
- [ ] Sandboxed server supervisor — Docker-based, §8.8
- [ ] Reference skill: github-repo-read
- [ ] Reference skill: slack-notify

**Exit demo:** topology reads GitHub repo via MCP → judge evaluates → audit records result. Kill MCP server mid-run; runtime reports failure gracefully.

### M6 — Observability + human interaction (NEW)

**Goal:** every runtime path is observable via OTel traces and CLI primitives. Local ring buffer preserves prompt privacy. Governance circuit breakers prevent runaway costs.

**Design reference:** `design/details/opentelemetry-observability.md`, `design/details/human-interaction-model.md`, `design/details/product-architecture-refinements.md`.

**Dependencies:** M5 (governance wiring needed for audit events).

**Features:**

- [ ] **OpenTelemetry Phase 1** — `SwarmKitTelemetry` class, trace-per-run, span-per-agent-step, tool call + governance child spans. `swarmkit.*` semantic attribute namespace.
- [ ] **OTel exporters** — `console` (human-readable to stderr), `otlp` (OTLP/HTTP async batching), `none` (default). Config via `~/.swarmkit/config.yaml` `telemetry:` block.
- [ ] **Local ring buffer** — SQLite-backed prompt/response store, keyed by OTel span ID. Configurable retention (default: 7 days). Survives process restarts.
- [ ] **`swarmkit debug`** — `--span-id`, `--run-id`, `--agent`, `--last N`. Retrieves prompts from local ring buffer.
- [ ] **AuditProvider abstraction** — `record()`, `query()`, `count()` methods. Built-ins: mock, sqlite (default), postgres, agt, plugin. Workspace config: `storage.audit`.
- [ ] **Per-skill audit redaction** — `audit:` block on skills with `log_inputs`, `log_outputs`, `redact` fields. Category-level defaults.
- [ ] **CLI primitives** — `swarmkit status` (snapshot), `swarmkit logs <run-id> [--follow]` (event tail), `swarmkit events [--filter]` (cross-run stream), `swarmkit stop <run-id>` (graceful shutdown), `swarmkit why <run-id>` (decision chain).
- [ ] **`swarmkit review`** — interactive TUI for pending HITL approvals (approve/reject/edit/skip).
- [ ] **`swarmkit ask`** — conversational observer, LLM-backed. Parses question → loads audit events → answers.
- [ ] **Notification plugin** — webhook-based, runs outside the SwarmKit runtime process. The runtime emits structured events; notification providers consume them via webhooks. Multiple providers can be configured in parallel. Built-ins: terminal (stdout, for local dev), slack (outgoing webhook), email (SMTP), generic webhook (configurable URL + payload template). Fires on: `hitl_requested`, `run_ended { status: error }`, `skill_gap_surfaced`. Workspace config specifies provider + endpoint + event filters.
- [ ] **Governance circuit breakers** — `max_steps_per_agent`, `max_steps_per_run`, `max_runs_per_topology_per_day`, `max_cost_per_run_usd`. Sensible defaults ship out of the box (e.g., `max_steps_per_run: 500`). Enforced in governance engine, not billing. From `market-analysis-and-risk-mitigations.md` Risk 3.
- [ ] **OTel metrics** — counters: `swarmkit.runs.total`, `swarmkit.agent.steps.total`, `swarmkit.tool.calls.total`. Histograms: `swarmkit.runs.duration_ms`, `swarmkit.tool.duration_ms`.
- [ ] **Execution detail API** — beyond high-level `swarmkit status`, users need root-cause access. `swarmkit logs <run-id> --follow` streams full execution detail (every agent step, tool call, governance decision). `swarmkit why <run-id>` traces the decision chain. `swarmkit debug --span-id <id>` retrieves actual prompts/responses from the local ring buffer. For HITL flows, the webhook payload includes the run ID + span ID so the reviewer can pull execution context via CLI or (in Rynko) via the dashboard trace view.

**Exit demo:** run a topology with `telemetry.exporter: console` — span output in terminal showing agent steps, tool calls, governance decisions. `swarmkit logs` tails the same run. `swarmkit debug --run-id xyz` retrieves prompt/response pairs from local SQLite. `swarmkit ask "what went wrong?"` answers from audit context. Optional: spin up local Jaeger, see the full trace.

### M7 — Intent drift detection (NEW)

**Goal:** detect semantic drift from original intent during multi-step execution; optionally nudge agents back on track.

**Design reference:** `design/details/intent-drift-detection.md`.

**Dependencies:** M6 (OTel spans for drift events, audit log for recording).

**Features:**

- [ ] **Schema extension** — `intent_monitoring:` block on agents and topology level. Fields: `enabled`, `threshold`, `on_drift` (log/warn/nudge).
- [ ] **IntentObserver** — `set_anchor(goal)`, `observe(step, output)` → `DriftResult`. Drift = `1 - cosine_similarity(anchor, output)`.
- [ ] **Embedding backend** — sentence-transformers default (local, no API keys). Pluggable via ModelProvider for API-based embeddings.
- [ ] **Drift strategies** — `log` (audit only), `warn` (log + emit warning event), `nudge` (inject system message reminding agent of original goal).
- [ ] **Tool error separation** — only score `agent_reasoning` events for drift. `tool_error` and `tool_response` excluded.
- [ ] **Audit integration** — drift scores as structured fields in audit events: `intent_drift: { score, threshold, action_taken }`.
- [ ] **OTel integration** — drift scores as span events with `swarmkit.drift.*` attributes.
- [ ] **OTel drift metrics** — histogram: `swarmkit.agent.drift.score`. Counter: drift threshold breaches.

**Not in scope:** `threshold: auto` self-learning mode. Needs feedback signal design, cold-start strategy, and run history storage (Rynko). See open questions in design note.

**Exit demo:** reference topology with `intent_monitoring: { enabled: true, threshold: 0.25, on_drift: nudge }`. Run it, show drift scores per step in CLI output. Demonstrate a nudge firing when an agent drifts past threshold. OTel console exporter shows drift events in spans.

---

## Phase 3 — Knowledge + Skills Ecosystem

Make swarms useful with real knowledge sources and a rich skill catalogue.

### M8 — Knowledge + skills ecosystem (enhance) 🟡

**Goal:** enhance existing knowledge server; add user knowledge server authoring; add skill registry CLI for community import.

**Design reference:** `design/details/knowledge-mcp-server.md`, `design/details/user-knowledge-server.md`, `design/details/skill-registry.md`.

**Dependencies:** M5 (MCP framework).

**What already exists:**

- [x] **Knowledge MCP server** — `swarmkit knowledge-server` is implemented (437 lines). Tools: `search_docs`, `get_schema`, `get_design_note`, `list_design_notes`, `list_schemas`, `get_error_reference`, `validate_workspace`, `list_reference_skills`, `write_workspace_file`, `read_workspace_file`, `run_pytest`. Keyword search with term-frequency scoring.
- [x] **Seed skills** — 20 reference skills already exist under `reference/skills/` (code-quality-review, security-scan, github-pr-read, audit-log-write, etc.)

**Features (remaining):**

- [ ] **User knowledge server** — `swarmkit author knowledge-server`. Generates a FastMCP server from user's docs/code/APIs. Three search tiers: keyword (default), TF-IDF (optional), vector (Qdrant, optional). Integrates into `swarmkit init` as a proactive knowledge question.
- [ ] **Skill registry CLI** — SKILL.md → SwarmKit YAML converter. CLI: `swarmkit skill install`, `swarmkit skill import <repo-url>`, `swarmkit skill import-mcp <repo-url>`, `swarmkit skill search`, `swarmkit skill list [--available]`.
- [ ] **Authoring AI integration** — authoring agent searches registry before generating, proposes installing existing skills when a match is found.
- [ ] **Knowledge server enhancements** — vector search (optional Qdrant tier), workspace-scoped knowledge queries.

**Exit demo:** `swarmkit skill search "code review"` finds community skills. `swarmkit skill install` adds to workspace. `swarmkit author knowledge-server` generates a server from user's API docs.

### M9 — Reference topologies (enhance) 🟡

**Goal:** make existing reference topologies production-quality and runnable end-to-end with real MCP servers.

**Dependencies:** M5 (MCP), M6 (observability), M7 (drift detection optional but adds value).

**What already exists:**

- [x] `reference/topologies/code-review.yaml` — topology YAML (62 lines)
- [x] `reference/topologies/skill-authoring.yaml` — topology YAML (30 lines)
- [x] 16 archetypes under `reference/archetypes/` (engineering-leader, qa-leader, ops-leader, supervisor-leader, security-reviewer, code-analyst, github-reader, llm-judge, authoring-supervisor, schema-drafter, test-writer, test-analyst, artifact-validator, artifact-publisher, conversation-leader, knowledge-searcher)
- [x] 20 skills under `reference/skills/` (code-quality-review, security-scan, github-pr-read, deploy-risk-review, qa-verdict, run-tests, lint-check, test-coverage-review, summarize-review, audit-log-write, etc.)
- [x] `design/details/topology-code-review.md` — design note
- [x] `design/details/topology-skill-authoring.md` — design note
- [x] `design/details/knowledge-curator-topology.md` — design note

**Features (remaining):**

#### Code Review Swarm — make runnable

- [ ] Wire to real MCP servers (GitHub MCP for PR reading)
- [ ] GitHub webhook handler trigger — kicks swarm on PR open
- [ ] Golden-path PR review end-to-end test with fixture PR
- [ ] HITL gate: deploy step pauses for human approval

**Exit demo:** `just demo-code-review` — fixture PR → leaders coordinate → deploy pauses for HITL → approve releases.

#### Skill Authoring Swarm — make runnable

- [ ] Wire multi-agent authoring flow (extends M3.5's single-agent `swarmkit author`)
- [ ] Authoring-provenance tagging — swarm-authored skills locked until human review
- [ ] End-to-end test: scripted conversation → valid skill file

**Exit demo:** `swarmkit author skill` — multi-agent swarm produces a new skill, tests it, publishes on approval.

#### Knowledge Curator Topology — implement

- [ ] Topology YAML with knowledge-coordinator, knowledge-curator, knowledge-indexer, knowledge-linter
- [ ] Wiki-fs MCP server skills (wiki-read, wiki-write, wiki-search, wiki-list, wiki-log)
- [ ] Ingest, query-and-persist, and lint operations

**Exit demo:** `swarmkit run . knowledge-curator --input "Ingest this Confluence page"` — curator reads source, creates wiki pages, rebuilds index.

---

## Phase 4 — Production Readiness

Ship-ready for open-source users. Everything needed for public launch.

### M10 — Eject + execution modes

**Goal:** all three execution modes from §14.1 + the eject escape hatch + canary deployments.

**Design reference:** §14.1, §14.4. `design/details/market-analysis-and-risk-mitigations.md` (canary feature adoption from AgentField analysis).

**Dependencies:** M6 (observability for canary health metrics), M7 (drift detection for canary promotion criteria).

**Features:**

- [ ] `design/eject.md` — generated project structure, dependency pinning, README template
- [ ] **`swarmkit eject <topology>`** — writes standalone LangGraph Python code. CLAUDE.md invariant #7. LangGraph platform risk mitigation.
- [ ] **FastAPI HTTP server** — `swarmkit serve` for persistent mode
- [ ] **Scheduler** — cron, webhook, file_watch triggers
- [ ] **Canary deployments** — topology-level version routing. Schema extension:

```yaml
deployment:
  strategy: canary
  versions:
    - version: "2.1.0"
      weight: 95
    - version: "2.2.0"
      weight: 5
      promote_when:
        drift_below: 0.20
        error_rate_below: 0.01
        min_runs: 100
```

- [ ] Ejected code runs without swarmkit installed — CI verification

**Exit demo:** eject the code-review swarm → install in fresh venv → runs without SwarmKit. `swarmkit serve` accepts HTTP triggers. Canary deployment routes 5% traffic to new version, drift metrics visible in OTel.

### M11 — Launch prep

**Goal:** public launch of SwarmKit as an open-source project.

**Features:**

- [x] ~16 archetypes already in `reference/archetypes/`
- [x] ~20 skills already in `reference/skills/`
- [ ] Review and polish existing catalogue — ensure all validate, have descriptions, and cover the §13.1 list
- [ ] Documentation site (MkDocs or Docusaurus)
- [ ] Docker image build + publish workflow
- [ ] PyPI + npm publish workflows with trusted publishing
- [ ] Schema hosting on `schemas.swarmkit.dev` (GitHub Pages)
- [ ] **Installable expertise packages Phase 1** — `swarmkit mcp-serve` exposes installed workspaces as MCP tools. `package.yaml` format. `swarmkit install`, `swarmkit publish` via GitHub releases. See `design/details/installable-expertise-packages.md`.
- [ ] CLI unimplemented stubs cleaned up — all commands either implemented or gracefully stubbed with milestone reference. See `design/details/cli-unimplemented-stubs.md`.
- [ ] Release notes

**Exit demo:** `pip install swarmkit` → `swarmkit init` → working swarm in <15 min. Public launch post. A first user with no prior context can follow the README to a running swarm.

---

## Rynko Platform (separate plan)

The commercial Rynko platform — UI dashboard, cloud telemetry, team features, self-learning intelligence — is out of scope for this plan. It has its own implementation plan in the Rynko repository.

Key design notes that inform the Rynko plan:

- `design/details/product-architecture.md` — open-source/commercial boundary, deployment models, revenue model
- `design/details/product-architecture-refinements.md` — local ring buffer, checkpointer for approval gates, OTLP/HTTP, usage-based pricing, unified workspace
- `design/details/opentelemetry-observability.md` — OTel Phase 2-3 (Rynko-specific: full metrics, cost attribution, sampling)
- `design/details/intent-drift-detection.md` — `threshold: auto` self-learning (needs Rynko for run history)
- `design/details/market-analysis-and-risk-mitigations.md` — competitive positioning, risk mitigations

## Deferred / future

Items explicitly not in this plan:

- **UI Testing Topology** — reference topology for vision-based browser testing via Playwright MCP. See `design/details/ui-testing-topology.md`. Deferred until Playwright MCP is mature.
- **Intent drift `threshold: auto`** — self-learning from historical run data. Needs feedback signal design, cold-start strategy, and Rynko for run history storage.
- **Secure local bridge** — localhost proxy for Rynko UI to pull prompts on-demand. v1.1+ Rynko feature.
- **Self-hosted UI** — enterprise-only, Phase 3 of Rynko. Docker/Helm deployment.
- **OTel Phase 3** — sampling strategies for high-volume topologies, Rynko ingestion optimisations.
- **Skill marketplace** — community ratings, trust scores. v1 is import-only.
- **Cross-topology agent communication** — explicitly not planned. Mesh discovery is a governance liability, not a feature. See `design/details/market-analysis-and-risk-mitigations.md` (AgentField analysis).
- **Installable expertise packages Phase 2-3** — public registry, dependency resolution, search/ratings. Phase 1 ships in M11.

## Design note index

Every design note under `design/details/` and where it appears in this plan:

| Design note | Milestone |
|-------------|-----------|
| `archetype-schema-v1.md` | M0 ✅ |
| `ci-pipeline.md` | Cross-cutting ✅ |
| `cli-unimplemented-stubs.md` | M11 |
| `conversational-authoring.md` | M3.5 ✅ |
| `dag-dependency-graph.md` | Phase 1 ✅ |
| `decision-skills.md` | M4 ✅ |
| `governance-provider-interface.md` | M2 ✅ |
| `hello-swarm-example.md` | M1 ✅ |
| `human-interaction-model.md` | M6 |
| `installable-expertise-packages.md` | M11 |
| `intent-drift-detection.md` | M7 |
| `knowledge-curator.md` | M9 |
| `knowledge-curator-topology.md` | M9 |
| `knowledge-mcp-server.md` | M8 (server implemented ✅, enhancements remaining) |
| `knowledge-pack-cli.md` | M1 ✅ |
| `langgraph-compiler.md` | M3 ✅ |
| `market-analysis-and-risk-mitigations.md` | M10 (canary), cross-cutting (risk awareness) |
| `mcp-client.md` | M5 🟡 |
| `model-provider-abstraction.md` | M2.5 ✅ |
| `model-provider-tool-calling.md` | M2.5 ✅ |
| `opentelemetry-observability.md` | M6 |
| `product-architecture.md` | Cross-cutting (scope boundary) |
| `product-architecture-refinements.md` | M6 (ring buffer, circuit breakers) |
| `pydantic-codegen.md` | M0 ✅ |
| `skill-registry.md` | M8 |
| `skill-schema-v1.md` | M0 ✅ |
| `structured-output-governance.md` | M4 ✅ |
| `swarmkit-validate-cli.md` | M1 ✅ |
| `topology-code-review.md` | M9 |
| `topology-loader.md` | M1 ✅ |
| `topology-schema-v1.md` | M0 ✅ |
| `topology-skill-authoring.md` | M9 |
| `trigger-schema-v1.md` | M0 ✅ |
| `ts-codegen.md` | M0 ✅ |
| `ui-testing-topology.md` | Deferred |
| `user-knowledge-server.md` | M8 |
| `workspace-schema-v1.md` | M0 ✅ |

## Open questions

| Question | Blocks |
|----------|--------|
| Sandboxing requirement for generated MCP servers | M5 |
| Governance CLI wiring (mock → AGT based on workspace config) | M5 |
| Audit log derived from OTel traces or separate system? | M6 |
| `swarmkit.cost.tokens` attribute on LLM spans (model provider cooperation) | M6 |
| Intent drift: nudge message customisable or generic? | M7 |
| Intent drift: per-agent vs topology-level "north star" anchor in DAG topologies? | M7 |
| Embedding default: sentence-transformers or TF-IDF (zero deps)? | M7 |
| Canary deployment: needs its own design note before implementation | M10 |
| Documentation site engine: MkDocs or Docusaurus? | M11 |
| Schema hosting domain resolution | M11 |
