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
| 1 | M2 | GovernanceProvider + AGT Tier 1 | ✅ | AGT policy denies + audits; CLI wires provider from workspace.yaml |
| 1 | M2.5 | ModelProvider abstraction | ✅ | Multi-provider topology loads and runs |
| 1 | M3 | LangGraph compiler | ✅ | `swarmkit run` executes two-agent swarm |
| 1 | M3.5 | Conversational authoring (v1) | ✅ | `swarmkit init` produces working workspace |
| 1 | M4 | Decision + persistence skills | ✅ | Structured output + LLM judge + review queue |
| 1 | — | DAG dependency graph | ✅ | Agents execute in dependency order |
| 1 | M5 | MCP integration | ✅ | MCP calls gated through governance, sandboxed execution |
| 2 | M6 | Observability + human interaction | ✅ | AuditProvider, OTel, ring buffer, circuit breakers, notifications, CLI rewrite, redaction |
| 2 | M6.5 | Workspace env configuration | ✅ | `workspace.env.yaml` + `SWARMKIT_ENV` switching |
| 2 | M7 | Intent drift detection | ✅ | IntentObserver, schema extension, compiler wiring, authoring integration |
| 3 | M8 | MCP integration layer | ✅ | Lazy startup, permission tiers, multimodal, document reader, MarkItDown |
| 3 | M9 | Reference topologies + structured delegation | 🟡 | Structured delegation ✅, structured comms ✅, Sterling log analyser ✅, reference topologies remaining |
| 4 | M10 | Serve + eject + canary | 🟡 | `swarmkit serve` ✅ (HTTP, auth, MCP, triggers). Eject + canary remaining |
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

### M2 — GovernanceProvider + AGT Tier 1 ✅

**Goal:** governance abstraction with real AGT policy engine for Tier 1 checks.

**Design reference:** §8.5, §8.6, §16.2, §16.3.

**Features:**

- [x] `governance-provider-interface.md` — interface stabilised
- [x] AGTGovernanceProvider (policy + audit + identity) — 194 lines, 10 integration tests
- [x] MockGovernanceProvider — used in all unit tests
- [x] Middleware pipeline for skill invocation — PR #43
- [x] Separation-of-powers integration tests
- [x] CLI governance provider wiring — `build_governance()` in `_workspace_runtime.py` reads `workspace.yaml` `governance:` block, instantiates `AGTGovernanceProvider.from_config()` when `provider: agt`, falls back to mock when unset.

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

### M5 — MCP integration ✅

**Goal:** real MCP servers power capability skills; governance gates every MCP call.

**Design reference:** §18. `design/details/mcp-client.md`.

**Features:**

- [x] MCPClientManager + stdio/SSE transports — PR #45
- [x] MCP server registry in workspace.yaml — PR #47, fixed PR #49
- [x] Schema↔runtime alignment + hello-world example — PR #49
- [x] `swarmkit author mcp-server` — conversational authoring
- [x] Knowledge Curator topology design — PR #46
- [x] Skill registry design — `design/details/skill-registry.md`
- [x] MCP calls gated through GovernanceProvider — `evaluate_action` before `call_tool` in `_skill_executor.py`. Action string: `mcp:call:<server>:<tool>`.
- [x] Sandboxed server supervisor — Docker-based (`_build_sandboxed_command` in `_client.py`). `--network=none`, workspace mounted read-only at `/workspace`, env vars injected via `-e`. Configurable image via `sandbox_image` or `SWARMKIT_SANDBOX_IMAGE` env var.
- [x] Reference skills: github-repo-read, github-pr-read, github-issue-read, slack-notify, and 16 more under `reference/skills/`

**Exit demo:** topology reads GitHub repo via MCP → judge evaluates → audit records result. Sandboxed servers run in Docker with no network access.

---

## Phase 2 — Runtime Completion (CURRENT PRIORITY)

Add observability, intent drift detection, and operational tooling. Everything here is open-source. These milestones make SwarmKit ready for real production workloads.

### M6 — Observability + human interaction (NEW)

**Goal:** every runtime path is observable via OTel traces and CLI primitives. Local ring buffer preserves prompt privacy. Governance circuit breakers prevent runaway costs.

**Design reference:** `design/details/opentelemetry-observability.md`, `design/details/human-interaction-model.md`, `design/details/product-architecture-refinements.md`.

**Dependencies:** none — M5 governance wiring is complete. Ready to start.

**Features:**

- [x] **OpenTelemetry Phase 1** — `SwarmKitTelemetry` class, trace-per-run, span-per-agent-step, tool call + governance child spans. `swarmkit.*` semantic attribute namespace. `telemetry/_tracer.py`.
- [x] **OTel exporters** — `console` (human-readable to stderr), `otlp` (OTLP/HTTP async batching), `none` (default). Config via `telemetry/_config.py`.
- [x] **Local ring buffer** — SQLite-backed prompt/response store, keyed by OTel span ID. Configurable retention (default: 7 days). WAL mode. `telemetry/_ring_buffer.py`.
- [x] **`swarmkit debug`** — `--span-id`, `--run-id`, `--agent`, `--last N`. Retrieves prompts from local ring buffer.
- [x] **AuditProvider abstraction** — `record()`, `query()`, `count()` methods. Built-ins: mock, sqlite (default). Registry system for plugins. `audit/_provider.py`.
- [x] **Per-skill audit redaction** — `audit:` block on skills with `log_inputs`, `log_outputs`, `redact` fields. Category-level defaults. Workspace-level clamping. `audit/_redact.py`.
- [x] **CLI primitives** — `swarmkit status` (snapshot), `swarmkit logs <run-id> [--follow]` (event tail), `swarmkit stop <run-id>` (graceful shutdown), `swarmkit why <run-id>` (decision chain). Note: `swarmkit events [--filter]` deferred — cross-run stream not yet needed.
- [x] **`swarmkit review`** — CLI-based HITL approval flow (`review list|show|approve|reject`). Full interactive TUI deferred to Rynko UI.
- [x] **`swarmkit ask`** — conversational observer, LLM-backed. Parses question → loads audit events → answers.
- [x] **Notification plugin** — webhook-based. Built-ins: terminal (stdout), slack, discord, telegram, generic webhook. Fires on: `hitl_requested`, `run_ended { status: error }`, `skill_gap_surfaced`. `notifications/`. Note: email (SMTP) provider deferred.
- [x] **Governance circuit breakers** — `max_steps_per_agent`, `max_steps_per_run` (default: 500). Enforced in governance engine. `governance/_limits.py`. Cost-based limit (`max_cost_per_run_usd`) plumbing exists but is inactive until provider-level cost extraction lands.
- [ ] **Provider-level cost extraction** — deferred. `CircuitBreakerTracker.add_cost()` exists but no provider wires cost_usd yet. Will activate `max_cost_per_run_usd` when implemented.
- [x] **OTel metrics** — counters: `swarmkit.runs.total`, `swarmkit.agent.steps.total`, `swarmkit.tool.calls.total`, `swarmkit.governance.decisions.total`, `swarmkit.agent.drift.breaches.total`. Histograms: `swarmkit.runs.duration_ms`, `swarmkit.tool.duration_ms`, `swarmkit.approval.wait_ms`, `swarmkit.agent.drift.score`. `telemetry/_metrics.py`.
- [x] **Execution detail API** — `swarmkit logs <run-id> --follow` streams full execution detail. `swarmkit why <run-id>` traces the decision chain. `swarmkit debug --span-id <id>` retrieves actual prompts/responses from the local ring buffer.

**Exit demo:** run a topology with `telemetry.exporter: console` — span output in terminal showing agent steps, tool calls, governance decisions. `swarmkit logs` tails the same run. `swarmkit debug --run-id xyz` retrieves prompt/response pairs from local SQLite. `swarmkit ask "what went wrong?"` answers from audit context. Optional: spin up local Jaeger, see the full trace.

### M6.5 — Workspace environment configuration (NEW)

**Goal:** separate environment-specific values (URLs, credentials, feature flags) from structural workspace config. Single interpolation point.

**Design reference:** `design/details/workspace-env-config.md`.

**Dependencies:** M6 complete. Cleans up how telemetry, notifications, and governance are configured before M7+ adds more.

**Features:**

- [x] **Property resolution engine** — two-phase: load `workspace.env.yaml`, then resolve `${ENV_VAR}` in property values. `resolver/_env_config.py`.
- [x] **Env file loading** — `workspace.env.yaml` (default) + `workspace.env.{SWARMKIT_ENV}.yaml` (per-environment override). Falls back to default if env-specific file missing.
- [x] **Resolver integration** — `_apply_env_interpolation()` runs after discovery, before schema validation. `resolver/__init__.py`.
- [ ] **`swarmkit init` update** — generates template `workspace.env.yaml` + adds `workspace.env*.yaml` to `.gitignore`. Deferred — init prompt mentions secrets but doesn't generate template file.
- [ ] **`swarmkit validate` update** — warns on unresolved `${property}` references. Deferred — no detection logic for leftover `${...}` patterns.
- [x] **Backward compatibility** — existing workspaces with inline values work unchanged. `interpolate_value()` no-ops when no `${...}` references exist.

**Exit demo:** same workspace runs against dev and prod with `SWARMKIT_ENV=dev` vs `SWARMKIT_ENV=prod`, each picking up different notification URLs and model providers from their env file.

### M7 — Intent drift detection (NEW)

**Goal:** detect semantic drift from original intent during multi-step execution; optionally nudge agents back on track.

**Design reference:** `design/details/intent-drift-detection.md`.

**Dependencies:** M6 (OTel spans for drift events, audit provider for recording).

**Features:**

- [x] **Schema extension** — `intent_monitoring:` block on agents and topology level. Fields: `enabled`, `threshold`, `on_drift` (log/warn/nudge).
- [x] **IntentObserver** — `set_anchor(goal)`, `observe(step, output)` → `DriftResult`. Drift = `1 - cosine_similarity(anchor, output)`.
- [x] **Embedding backend** — sentence-transformers default (local, no API keys). Pluggable via ModelProvider for API-based embeddings.
- [x] **Drift strategies** — `log` (audit only), `warn` (log + emit warning event), `nudge` (inject system message reminding agent of original goal).
- [x] **Tool error separation** — only score `agent_reasoning` events for drift. `tool_error` and `tool_response` excluded. `_is_error_passthrough()` in `_drift.py`.
- [x] **Audit integration** — drift scores as structured fields in audit events: `intent_drift: { score, threshold, action_taken }`.
- [x] **OTel integration** — drift scores as span events with `swarmkit.drift.*` attributes.
- [x] **OTel drift metrics** — histogram: `swarmkit.agent.drift.score`. Counter: `swarmkit.agent.drift.breaches.total`. `telemetry/_metrics.py`.

- [x] **Authoring integration** — `swarmkit init` and `swarmkit author topology` ask if the user wants intent monitoring. If yes, add `intent_monitoring: { enabled: true, threshold: 0.75, on_drift: log }`. If no, add as a commented-out block so users discover the feature.

**Not in scope:** `threshold: auto` self-learning mode. Needs feedback signal design, cold-start strategy, and run history storage (Rynko). See open questions in design note.

**Exit demo:** reference topology with `intent_monitoring: { enabled: true, threshold: 0.75, on_drift: nudge }`. Run it, show drift scores per step in CLI output. Demonstrate a nudge firing when an agent drifts past threshold. OTel console exporter shows drift events in spans.

---

## Phase 3 — Knowledge + Skills Ecosystem

Make swarms useful with real knowledge sources and a rich skill catalogue.

### M8 — MCP integration layer ✅

**Goal:** make the agent-to-tool interface clean, filtered, and governed. Lazy server startup, permission tiers, multimodal document understanding.

**Design reference:** `design/details/mcp-discovery-pattern.md`, `design/details/document-reader-mcp.md`.

**Dependencies:** M5 (MCP framework).

**What already exists:**

- [x] **Per-agent tool filtering** — the compiler's `_build_tools()` already creates `ToolSpec` entries only for the agent's own skills.
- [x] **Knowledge MCP server** — `swarmkit knowledge-server` with domain-tagged corpus, TF-IDF search.
- [x] **Seed skills** — 20 reference skills under `reference/skills/`.

**Features (shipped):**

- [x] **Lazy MCP server startup** — `start_required(topology)` only starts servers the topology needs. PR #123.
- [x] **Permission tiers on MCP servers** — `permission: open|cautious|strict|readonly` per server with per-tool overrides. PR #124.
- [x] **Document reader MCP server** — `view_image` (ImageContent for vision models), draw.io/SVG parsing, CSV, text, file discovery. Complements MarkItDown for document text. PR #125.
- [x] **Multimodal model provider support** — image content blocks wired through all 7 providers. `image_block(path)` helper. PR #126.
- [x] **Compiler image wiring** — skill-driven image flow (Option C). MCP tools returning ImageContent flow through tool results to the model. MarkItDown integration for document reading. PR #127.

**Deferred to future milestones:**

- **MCP gateway prototype** — single server wrapping all workspace servers with namespace routing. Needs more design discussion.
- **User knowledge server** — `swarmkit author knowledge-server`. Independent of MCP integration layer.
- **Skill registry CLI** — dropped as premature. Revisit after gateway is designed.
- **Authoring AI integration** — depends on registry, deferred.

**Exit demo:** `swarmkit run` with multimodal-test example — coordinator (llama-3.3) delegates to vision agent (claude-sonnet) which calls `view_image` via MCP skill and produces detailed architecture diagram analysis.

### M9 — Reference topologies (enhance) 🟡

**Goal:** make existing reference topologies production-quality and runnable end-to-end with real MCP servers.

**Dependencies:** M5 ✅ (MCP), M6 (observability), M7 (drift detection optional but adds value).

**What already exists:**

- [x] `reference/topologies/code-review.yaml` — topology YAML (62 lines)
- [x] `reference/topologies/skill-authoring.yaml` — topology YAML (30 lines)
- [x] 16 archetypes under `reference/archetypes/` (engineering-leader, qa-leader, ops-leader, supervisor-leader, security-reviewer, code-analyst, github-reader, llm-judge, authoring-supervisor, schema-drafter, test-writer, test-analyst, artifact-validator, artifact-publisher, conversation-leader, knowledge-searcher)
- [x] 23 skills under `reference/skills/` (code-quality-review, security-scan, github-pr-read, deploy-risk-review, qa-verdict, run-tests, lint-check, test-coverage-review, summarize-review, audit-log-write, grounding-verifier, contradiction-detector, citation-checker, etc.)
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

#### Logistics Control Tower — implement (after M6+M7)

A reference topology demonstrating DAG execution, HITL approval gates, governance scopes, intent drift detection, and structured output in a single end-to-end example. Inspired by [ai-logistics-control-tower](https://github.com/sarthakshirsatai-lab/ai-logistics-control-tower).

- [ ] Design note: `design/details/logistics-control-tower.md`
- [ ] Topology YAML — orchestrator + 4 workers (exception-detector, courier-scorer, resolution-agent, communication-agent) with `depends_on` DAG
- [ ] HITL gate on resolution agent: auto-execute low-risk, escalate high-cost/VIP/unprecedented to `swarmkit review`
- [ ] Governance scopes: `logistics:auto-reroute` (agent), `logistics:refund` (human-only)
- [ ] Intent drift monitoring: detect if resolution agent starts auto-executing what it should escalate
- [ ] Structured output schema for exception alerts (shipment ID, severity, action, timestamp)
- [ ] MCP servers: shipment tracker (simulated), courier performance DB, notification service
- [ ] Circuit breaker: `max_cost_per_run_usd` prevents runaway rerouting

**Dependencies:** M6 (HITL notifications, circuit breakers), M7 (intent drift detection).

**Exit demo:** `swarmkit run examples/logistics-control-tower/ control-tower --input "Process today's exceptions"` — detects exceptions → scores couriers → resolves (some auto-execute, some escalate to HITL) → sends customer notifications. Drift detection flags if the resolution agent drifts from its mandate.

#### Structured delegation model (v2 compiler) ✅

Replaced free-text `delegate_to_*` with planner-driven task execution. See `design/details/structured-delegation.md`.

**Shipped (v1.2.0 – v1.2.8):**

- [x] **`create-task-plan` + `update-task-plan` + `read-task-result`** — compiler-injected tools for agents with 2+ children (PR #176)
- [x] **Task execution engine** — parallel batch execution, self-tasks, dependency ordering (PR #178)
- [x] **Checkpoint review loop** — coordinator reviews findings, modifies plan at checkpoints (PR #179)
- [x] **Summary-first child results** — 3-5 bullet key_findings via LLM, full results on disk (PR #180)
- [x] **Init-read + resume from disk** — tasks.json survives crashes, CLI detects previous plans (PR #181)
- [x] **Auto-fix dependencies** — self/document-writer tasks auto-depend on research tasks (PR #183)
- [x] **Auto-add synthesis** — compiler adds synthesis task when model omits it (PR #184)
- [x] **read-task-result in tool loop** — no longer causes re-delegation loop (PR #187)
- [x] **Compiler split** — 1854-line file split into 8 modules under 500 lines each (PR #177)
- [x] **Sterling workspace v3.0** — 6 workers (jira, config, docs, developer, log-analyst, document-writer)

**Sterling-specific features shipped:**

- [x] **Atlassian wrapper MCP** — structured input, no JQL/CQL syntax needed (PR #169)
- [x] **Log analyser MCP** — SQLite-indexed, 500MB+ support, 9 tools, TIMER/SQLDEBUG parsing (PR #186)
- [x] **Log-analyst archetype** — dedicated agent with 8-step mandatory workflow (PR #189)
- [x] **Document writer** — pandoc MCP for DOCX/PDF generation (PR #156)

**Exit demo:** Sterling assistant creates task plan → log-analyst produces grounded 200+ line analysis with real SQL, call trees, timestamps → architect synthesizes → document-writer formats. All persisted to disk, resumable on crash.

#### Governance decision skills (grounding) ✅

Mandatory decision skills with workspace/topology merge semantics. See `design/details/governance-decision-skills.md`.

**Shipped (v1.2.12 – v1.2.13):**

- [x] **Schema + types + merge logic** — `decision_skills` in workspace governance + topology governance blocks (PR #201)
- [x] **Compiler trigger points** — post_output, checkpoint, pre_synthesis hooks fire at correct moments (PR #204)
- [x] **SkillBackedGovernanceProvider** — wraps base provider, invokes decision skills via llm_prompt (PR #205)
- [x] **Retry loop** — failed post_output re-prompts agent with skill feedback, up to 4 attempts (PR #205)
- [x] **Reference skills** — grounding-verifier, contradiction-detector, citation-checker (PR #201)
- [x] **Sterling integration** — grounding-verifier + contradiction-detector wired, archetype grounding rules (PR #205)

**Architecture:** governance layer enforces, compiler stays topology-agnostic. Rynko Flow validation gates use the same mechanism (governance decision skills with `mcp_tool` implementation type).

**Exit demo:** Sterling researcher fabricates a name → grounding-verifier catches it → agent revises → clean output passes through.

#### Structured inter-agent communication ✅

Replace prose between agents with structured JSON. Research-backed: 55-87% token reduction + 3-36% accuracy improvement. See `design/details/structured-inter-agent-communication.md`.

**Architecture:** three layers — MCP provenance envelope (Phase A), default output_schema for workers, validation (Tier 1 deterministic + Tier 2 Rynko opt-in).

**Wave 1 — Provenance + structured output (v1.2.26–v1.2.29):**
- [x] **MCP provenance envelope** — `ToolMetadata` + `ToolResponse` in `MCPClientManager.call_tool`. PR #223.
- [x] **Default output_schema for workers** — all workers produce structured JSON by default. JSON mode from provider. Schema validation + retry. PR #222.
- [x] **Auto-populate source from tool metadata** — `validate_and_fill_sources()` in `_output_schema.py`. PR #224.
- [x] **Skip summarizer for structured output** — structured data IS the summary. Landed in PR #222.
- [x] **Structured checkpoint instructions** — checkpoint prompts replaced with JSON action specs. PR #225.

**Wave 2 — Governance integration (v1.2.30–v1.2.31):**
- [x] **Deterministic grounding (Tier 1)** — `check_grounding()` validates all findings have non-empty source. No LLM needed. PR #227.
- [x] **MCP-backed decision skills** — any MCP server can be a governance decision skill. PR #228.

**Wave 3 — Sterling + authoring + gate-validator (v1.2.32):**
- [x] **Sterling structured researchers + authoring** — document-writer opts out with `output_schema: null`. PR #230.
- [x] **Gate-validator MCP server** — `list_gates` + `validate_gate` tools, JSON Schema validation gates. PR #231.

**Future (M10/M11):** Full MCP gateway replaces Phase A proxy. See `mcp-discovery-pattern.md`.

#### Reference topologies — remaining

- [ ] **Code Review Swarm** — wire to real GitHub MCP, HITL gate, e2e test
- [ ] **Skill Authoring Swarm** — multi-agent authoring flow, provenance tagging
- [ ] **Knowledge Curator** — topology + wiki-fs MCP server
- [ ] **Logistics Control Tower** — DAG + HITL + drift demo topology

---

## Phase 4 — Production Readiness

Ship-ready for open-source users. Everything needed for public launch.

### M10 — Serve + eject + canary 🟡

**Goal:** all three execution modes from §14.1 + the eject escape hatch + canary deployments.

**Design reference:** §14.1, §14.4. `design/details/serve-and-auth.md`. `design/details/market-analysis-and-risk-mitigations.md` (canary feature adoption from AgentField analysis).

**Dependencies:** M6 ✅ (observability for canary health metrics), M7 ✅ (drift detection for canary promotion criteria).

#### Serve mode ✅ (v1.2.53–v1.2.57, PRs #262–#266)

- [x] **FastAPI HTTP server** — `swarmkit serve` for persistent mode. Async job execution, polling, SSE streaming. `server.py`. PR #262.
- [x] **MCP endpoint** — Streamable HTTP at `/mcp`. Each topology becomes an MCP tool. PR #262, lifecycle PR #264.
- [x] **AuthProvider abstraction** — `NoneAuthProvider` (default), `APIKeyAuthProvider`, `JWTAuthProvider` (JWKS auto-discovery). `auth/`. PRs #263, #265.
- [x] **Server config** — `server:` block in workspace.yaml. `jobs.max_concurrent`, `jobs.timeout_seconds`, `mcp.enabled`. Semaphore-based concurrency limiting. PR #264.
- [x] **Trigger scheduler** — cron scheduler with `croniter` (optional dep), webhook HMAC-SHA256 signature validation. `triggers/`. PR #266.
- [x] **Conversation endpoints** — create, list, send message via HTTP. PR #262.
- [x] **Serve CLI reference** — real test outputs documented at `docs/reference/serve-cli-tests.md`. PR #267.

#### Eject (remaining)

- [ ] `design/eject.md` — generated project structure, dependency pinning, README template
- [ ] **`swarmkit eject <topology>`** — writes standalone LangGraph Python code. CLAUDE.md invariant #7. LangGraph platform risk mitigation.
- [ ] Ejected code runs without swarmkit installed — CI verification

#### Canary deployments (remaining)

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

**Exit demo:** eject the code-review swarm → install in fresh venv → runs without SwarmKit. `swarmkit serve` accepts HTTP triggers ✅. Canary deployment routes 5% traffic to new version, drift metrics visible in OTel.

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
| `mcp-client.md` | M5 ✅ |
| `mcp-discovery-pattern.md` | M8 |
| `model-provider-abstraction.md` | M2.5 ✅ |
| `model-provider-tool-calling.md` | M2.5 ✅ |
| `opentelemetry-observability.md` | M6 |
| `pre-input-decision-gate.md` | M9 ✅ |
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
| `workspace-env-config.md` | M6.5 |
| `workspace-schema-v1.md` | M0 ✅ |
| `structured-delegation.md` | M9 ✅ |
| `governance-decision-skills.md` | M9 ✅ |
| `scope-freeze-and-spec-conformance.md` | M9 ✅ |
| `two-phase-execution-flow.md` | M9 ✅ |
| `serve-and-auth.md` | M10 ✅ |
| `structured-inter-agent-communication.md` | M9 ✅ |
| `document-writer-pattern.md` | M8 ✅ |

## Open questions

| Question | Blocks |
|----------|--------|
| ~~Sandboxing requirement for generated MCP servers~~ | ~~M5~~ — resolved: Docker-based, `--network=none`, read-only mounts |
| ~~Governance CLI wiring (mock → AGT based on workspace config)~~ | ~~M5~~ — resolved: `build_governance()` reads workspace.yaml |
| Audit log derived from OTel traces or separate system? | M6 |
| `swarmkit.cost.tokens` attribute on LLM spans (model provider cooperation) | M6 |
| Intent drift: nudge message customisable or generic? | M7 |
| Intent drift: per-agent vs topology-level "north star" anchor in DAG topologies? | M7 |
| Embedding default: sentence-transformers or TF-IDF (zero deps)? | M7 |
| Canary deployment: needs its own design note before implementation | M10 |
| Documentation site engine: MkDocs or Docusaurus? | M11 |
| Schema hosting domain resolution | M11 |
