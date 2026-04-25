---
title: Implementation Plan — SwarmKit v1.0
description: Phased roadmap decomposing design §20.1 into eleven milestones, each with a concrete exit demo.
tags: [plan, milestones, roadmap]
status: active
---

# Implementation Plan — SwarmKit v1.0

**Source of truth:** `design/SwarmKit-Design-v0.6.md` (§20.1 lists the Phase 1 scope). This plan decomposes that scope into milestones and features, each of which becomes one or more PRs under the [feature delivery workflow](../CLAUDE.md#feature-delivery-workflow--mandatory).

**Status:** drafted 2026-04-21, updated 2026-04-25. Living document — update as milestones land. M0–M4 complete; M2.5 complete; M3.5 complete; M5 ~70% done.

## How this plan works

- **Milestones** are coarse checkpoints. Each milestone has an **exit criterion** — something demonstrable a human can watch.
- Each milestone contains a set of **features**. One feature = one design note at `design/details/<slug>.md` + one implementation PR (or a design PR followed by an implementation PR for larger features).
- Milestones are mostly sequential but features within a milestone often parallelise. Dependencies are noted per-feature.
- Effort estimates below mirror §20.1 (Phase 1: 13–16 weeks). Not commitments — they inform ordering, not a schedule.

## Milestone overview

| # | Milestone | Exit demo |
|---|-----------|-----------|
| 0 ✅ | Schemas nailed down (done 2026-04-21) | `just demo-schema` loads every fixture in both Python and TS with full drift protection. |
| 1 ✅ | Topology loading & resolution | `swarmkit validate path/to/topology.yaml` prints a resolved tree; archetype and skill refs resolved. |
| 2 | `GovernanceProvider` abstraction + AGT Tier 1 | Policy decisions roundtrip through `AGTGovernanceProvider` for a real scope check; mock provider used in unit tests. |
| 2.5 ✅ | `ModelProvider` abstraction | Topology with two agents on different providers (e.g. Anthropic leader + Ollama worker) loads, `just demo-model-providers` prints green per installed provider. |
| 3 ✅ | LangGraph compiler — capability + coordination | `swarmkit run hello-topology.yaml` executes a two-agent swarm and prints the final state. |
| 3.5 ✅ | Conversational authoring (v1) | `swarmkit init` in an empty dir produces a working workspace through conversation. |
| 4 ✅ | Decision + persistence skills | Same topology, now with an LLM judge and an audit-log write; audit entries survive process restart. |
| 5 | MCP integration | Topology calls a real MCP server (filesystem or a simple HTTP tool); AGT security gateway wraps the call. |
| 6 | Reference: Code Review Swarm | `just demo-code-review` runs the full swarm against a fake PR and produces a review verdict + HITL gate. |
| 7 | Reference: Skill Authoring Swarm | `swarmkit author skill` launches a conversational chat that produces a tested, published skill. |
| 8 | Reference: Workspace Authoring Swarm | `swarmkit init` in an empty dir produces a working workspace through conversation. |
| 9 | Eject + HTTP server + scheduled mode | `swarmkit eject` produces runnable LangGraph code; `swarmkit serve` accepts HTTP triggers. |
| 10 | Catalogue polish + launch prep | ~15 archetypes, ~20 skills, docs site, Docker image, PyPI publish dry-run. |

## Cross-cutting workstreams

Run in parallel with the milestones above:

- **Docs:** concept pages for topology / agents / skills / archetypes / governance land as soon as their milestone does. One docs PR per concept. Machine migration + local LLM setup guide landed (2026-04-25).
- **CI: ✅ DONE.** GitHub Actions pipeline (lint + typecheck + test on push/PR; matrix on py 3.11, 3.12, 3.13 + JS + schema codegen drift + JSON Schema validity). Design note at `design/details/ci-pipeline.md`. PR #2.
- **Packaging:** PyPI + npm + Docker publish workflows finalised in milestone 10. Trial runs in milestone 5+.
- **Governance hardening:** every milestone that touches `governance/` is reviewed against §8 Separation of Powers invariants.
- **LLM-friendly knowledge (new).** Usability is a product feature — SwarmKit docs are consumed primarily by LLMs on behalf of users. Every milestone maintains: (a) `llms.txt` current as new docs land; (b) frontmatter on new design notes (see `docs/notes/llm-friendly-knowledge.md`); (c) error messages readable-as-docs; (d) a usability-first review pass per PR (see `docs/notes/usability-first.md`). Task #23 (`swarmkit validate` human-readable errors) blocks M1 completion. Task #24 (`swarmkit knowledge-pack` CLI) lands in M1. Task #25 (knowledge MCP server design) targets M5. Task #26 (authoring-swarm continuation past init) is an M8 design question.
- **Schema hosting.** JSON Schemas declare `$id` URLs under `schemas.swarmkit.dev/v1/*.schema.json`. Until hosted, any tool doing remote `$ref` resolution fails. Promoted from M10 to a blocking task for v1.0 launch; practical path is GitHub Pages under a controlled domain. Tracked as future task when the domain question is resolved.
- **Observability (new, v1.0 must-ship).** Every runtime path emits structured audit events; the CLI provides status / logs / events / review / stop / why; `swarmkit ask` gives a conversational observer. See `design/details/human-interaction-model.md` and `docs/notes/observability.md`. Task #33 is the design note (done). Skills emit via `GovernanceProvider.record_event` only; storage is a workspace-level choice configured under `storage.audit` (uniform `{provider, provider_id?, config}` shape matching SecretsProvider). The full M2 observability bundle: audit event schema + per-skill `audit:` block as a schema-change-discipline PR (#34), `AuditProvider` ABC with sqlite/postgres/agt/plugin built-ins (#38), workspace.audit schema update (#39), `design/details/audit-provider.md` detailed note (#40). M4 picks up CLI primitives (#35), `swarmkit ask` (#36), notification plugin (#37). None deferred to v1.1 — the UI (v1.1) is a *second* front-end over the same event stream.

---

## Milestone 0 — Schemas

**Goal:** every artifact example in the v0.6 design doc (topology, skill, archetype, workspace, trigger) validates cleanly in both the Python and TS validator. Codegen Pydantic models + TS types from the schemas. This is the foundation — nothing else is worth building until the schemas are firm.

**Design reference:** §6.3, §10, §13, §9.3.

**Status: ✅ COMPLETE** (2026-04-21). All features landed via PRs #5–#13; M0 exit demo verified via `just demo-schema` across both languages.

**Features:**

- [x] `design/details/topology-schema-v1.md` — PR #5.
- [x] `design/details/skill-schema-v1.md` — PR #8.
- [x] `design/details/archetype-schema-v1.md` — PR #9.
- [x] `design/details/workspace-schema-v1.md` — PR #10.
- [x] `design/details/trigger-schema-v1.md` — PR #11.
- [x] `feat(schema): pydantic model codegen from JSON Schema` — PR #12. Generated models live in `swarmkit_schema.models`.
- [x] `feat(schema): typescript type codegen from JSON Schema` — PR #13. Generated types re-exported from `@swarmkit/schema`.
- [x] `test(schema): round-trip every v0.6 example` — 182 Python / 108 TS tests pass across all fixtures.

**Exit demo (verified):** `just demo-schema` loads every valid + invalid fixture across all five schemas in both Python and TypeScript and prints a green validation report. Drift protection (`just schema-codegen-check`) runs in CI on every PR.

## Milestone 1 — Topology loading & resolution

**Goal:** given a workspace directory, load and fully resolve every topology, archetype, and skill file. Resolved topology is a typed Pydantic model; CLI can print it.

**Design reference:** §10, §14.3 (steps 1–3).

**Status: ✅ COMPLETE** (2026-04-21). All features landed via PRs #18–#23; M1 exit demo verified via `just demo-resolver`.

**Features:**

- [x] `feat(runtime): workspace directory loader` — PR #18. Enumerates topologies/, archetypes/, skills/; reports conflicts.
- [x] `feat(runtime): archetype + skill resolvers` — PRs #20, #21. Merges archetype defaults, validates skill refs, detects composed-skill cycles.
- [x] `feat(runtime): ResolvedTopology data model` — PR #21. Frozen dataclass tree consumed by downstream compilers.
- [x] `feat(cli): swarmkit validate <path>` + `human-readable validate errors` — PR #23 (tasks #31 + #23). `--json`/`--tree`/`--quiet`/`--color` flags; errors carry file, JSON pointer, rule id, suggestion.
- [x] `feat(example): hello-swarm on-ramp + demo-resolver` — this PR. Valid + deliberately-broken variants under `examples/hello-swarm/`; `just demo-resolver` runs both.
- [x] `feat(cli): swarmkit knowledge-pack` — **task #24**, this PR. Bundles the SwarmKit corpus + optional workspace + validation state into a paste-ready markdown prompt (~350 KB). Auto-discovers `design/details/*.md` and `docs/notes/*.md` so new notes land without editing the CLI.
- [ ] `test(runtime): resolve every reference/ artifact` — gated on the v1.0 reference topologies landing (`reference/` currently empty). Tracked as a follow-up of the reference-topology authoring work.

**Exit demo (verified):** `just demo-resolver` validates `examples/hello-swarm/workspace/` (exit 0, resolved tree printed) and `examples/hello-swarm/workspace-broken/` (exit 1, `agent.unknown-archetype` error with file pointer + suggestion). A first-time user understands the deliberate failure from the error alone.

## Milestone 2 — GovernanceProvider + AGT Tier 1

**Goal:** lock in the abstraction; wire AGT's Agent OS policy engine for deterministic Tier 1 checks; introduce a `MockGovernanceProvider` for tests.

**Design reference:** §8.5, §8.6 Tier 1, §16.2, §16.3.

**Status: 🟡 PARTIAL.** The `GovernanceProvider` interface, `MockGovernanceProvider`, and basic middleware pipeline shipped as prerequisites for M3/M4. AGT integration (real policy engine) is the remaining work.

**Features:**

- [x] `design/governance-provider-interface.md` — interface defined in `governance/` module; method signatures (`evaluate_action`, `record_event`, `get_trust_score`) stabilised through M3–M4 usage.
- [ ] `feat(governance): AGTGovernanceProvider policy evaluation` — wraps agent-os-kernel for scope checks.
- [ ] `feat(governance): AGTGovernanceProvider audit` — wraps Agent SRE for append-only event recording.
- [x] `feat(governance): MockGovernanceProvider` — deterministic, assertable, used in all unit tests and the `swarmkit run` CLI path.
- [x] `feat(runtime): middleware pipeline for skill invocation` — every skill call routes through `evaluate_action` before execution (wired in M4, PR #43).
- [ ] `test(governance): separation-of-powers invariants` — executive cannot modify audit, cannot bypass policy. Blocked on AGT integration.

**Exit demo:** a unit-test swarm where a worker tries to invoke a skill it lacks the scope for; policy denies; audit records the attempt; test asserts both.

## Milestone 2.5 — Model provider abstraction

**Goal:** topology YAML picks an LLM provider per agent (Anthropic, OpenAI, Google, Ollama, custom); the runtime dispatches through a `ModelProvider` interface with built-in implementations and a plugin path. Mirror of `GovernanceProvider`. Blocks M3 because the compiler needs a dispatch seam.

**Design reference:** `design/details/model-provider-abstraction.md`; mirrors §8.5.

**Status: ✅ COMPLETE.** All built-in providers shipped and are exercised by `swarmkit run` and `swarmkit init`. Provider registry with env-var auto-discovery works. `SWARMKIT_PROVIDER` / `SWARMKIT_MODEL` env-var overrides work.

**Features:**

- [x] `design/details/model-provider-abstraction.md` — ABC, built-in providers, registration, credentials contract. PR #7.
- [x] `feat(runtime): ModelProvider ABC + MockModelProvider + AnthropicModelProvider` — landed across M3 PRs.
- [x] `feat(runtime): OpenAI + Google + Ollama + OpenRouter + Groq + Together providers` — all seven built-in providers implemented. Ollama tool-calling fix in PR #36.
- [x] `design/details/model-provider-tool-calling.md` — canonical tool-call format and per-provider translation.
- [x] `feat(runtime): provider registry + env-var discovery + SWARMKIT_PROVIDER/SWARMKIT_MODEL overrides`.
- [x] `test(runtime): topology with a non-registered provider fails load with a clear error`.

**Exit demo (verified):** `swarmkit run` dispatches to whichever provider has credentials in the environment. `SWARMKIT_PROVIDER=google SWARMKIT_MODEL=gemini-2.5-flash` overrides per-agent provider declarations. Missing SDKs / creds are skipped at registration time.

## Milestone 3 — LangGraph compiler (capability + coordination)

**Goal:** topology → `StateGraph` for the simple cases: capability skills (straight-line tool calls) and coordination skills (parent→child, peer→peer A2A). Checkpointing to SQLite.

**Design reference:** §14.3, §14.5, §5.3.

**Status: ✅ COMPLETE** (PRs #35 and related fixes). `swarmkit run` executes two-agent topologies end-to-end. Delegation, skill dispatch, and synthesis all work.

**Features:**

- [x] `design/langgraph-compiler.md` — translation rules documented in `design/details/langgraph-compiler.md`.
- [x] `feat(compiler): node construction from agent` — one LangGraph node per agent, hooked to governance middleware. PR #35.
- [x] `feat(compiler): edge construction from hierarchy` — parent/child edges via `delegate_to_<child>` tool calls. PR #35.
- [x] `feat(compiler): coordination skill dispatch` — delegation-based handoff via StateGraph edges. PR #35.
- [x] `feat(compiler): capability skill dispatch` — `mcp_tool` + `llm_prompt` skill execution wired through `_skill_executor.py`. PR #35, refined in M4/M5.
- [x] `feat(runtime): SQLite checkpointer wiring` — per-topology checkpoint file under `.swarmkit/state/`.
- [x] `feat(cli): swarmkit run <topology>` — one-shot execution (§14.1). PR #35.

**Exit demo (verified):** `swarmkit run examples/hello-swarm/workspace hello` — root supervisor delegates to greeter worker, worker executes, root synthesises final output. `just demo-run` runs the full flow.

## Milestone 3.5 — Conversational authoring (v1)

**Goal:** users describe what they want in natural language; a single conversational agent asks clarifying questions, generates YAML artifacts, validates in real-time, and writes files on approval. **The user never writes YAML.** This is the primary user interface ��� moved from M7-M8 because conversational authoring is the product, not a late-stage feature.

**Design reference:** §11, §12, §14.2, `design/details/conversational-authoring.md`.

**Status: ✅ COMPLETE** (PR #37, hardened by PRs #36, #48, and related fixes). `swarmkit init` and `swarmkit author` both work. `swarmkit author mcp-server` also shipped as part of M5.

**Features:**

- [x] `design/details/conversational-authoring.md` — conversation flow, tools, system prompt, provider resolution.
- [x] `feat(authoring): authoring agent loop + tools` — validate_yaml, write_files, read_workspace, list_schemas. PR #37.
- [x] `feat(cli): swarmkit init` — interactive workspace creation from scratch. PR #37.
- [x] `feat(cli): swarmkit author topology/skill/archetype` — interactive artifact authoring in an existing workspace. PR #37. Multiple hardening fixes: YAML extraction from code blocks (PR #48), graceful fallback when model skips tool calling, stronger prompts for detailed archetypes.
- [x] `feat(cli): swarmkit author mcp-server` — conversational MCP server authoring. Landed as part of M5 work.

**Exit demo (verified):** `swarmkit init` — user answers questions, gets a working workspace with topology + archetypes + skills. `swarmkit validate` passes. `swarmkit run` produces output.

## Milestone 4 — Decision + persistence skills

**Goal:** LLM judge skills (Tier 2), deterministic validator skills, audit-log writes, review-queue primitive, skill-gap log primitive.

**Design reference:** §6.2, §8.6 Tier 2/3, §12.1, §14.5, §17.

**Status: ✅ COMPLETE** (PRs #38–#44). Structured output governance, decision skills, review queue, skill gap log, inline HITL, panel aggregation, and trust scoring all shipped.

**Features:**

- [x] `design/structured-output-governance.md` — **task #43.** Deterministic output validation + auto-correction. PR #38.
- [x] `design/decision-skills.md` — how verdicts and confidence scores flow through state. PR #40.
- [x] `feat(skills): llm-judge primitive skill` — rubric-driven; returns verdict+confidence. PR #43.
- [x] `feat(skills): schema-validator primitive skill` — deterministic, jsonschema-backed. PR #38.
- [x] `feat(runtime): structured output enforcement` — compiler reads skill `outputs` block, wires provider JSON mode + schema validation + retry-with-field-errors into the agent's tool-use loop. PRs #38, #39.
- [x] `feat(skills): multi-persona panel composition (Tier 3)` — fan-out + consensus. PR #43.
- [x] `feat(runtime): review queue primitive` — file-backed in v1.0, pluggable storage. PR #41.
- [x] `feat(runtime): skill gap log primitive` — automatic entry when HITL thresholds are crossed (§12.1). PR #41.
- [x] `feat(runtime): inline HITL + review/gaps CLI commands` — `swarmkit review`, `swarmkit gaps`. PR #42.
- [x] `feat(governance): AGT trust scoring integration` — trust-score decay on repeated judicial escalations. PR #44.

**Exit demo (verified):** skills with declared `outputs` schemas produce valid structured output. Auto-correction loop fixes invalid fields on retry. Decision skills return verdicts; low-confidence verdicts land in the review queue. HITL notification design documented (PR #42).

## Milestone 5 — MCP integration

**Goal:** real MCP servers power capability skills; AGT security gateway wraps every MCP call.

**Design reference:** §18.

**Status: 🟡 IN PROGRESS (~70%).** Core MCP plumbing is done — stdio + HTTP transports, workspace config, tool-schema forwarding, hello-world end-to-end demo. Knowledge Curator design landed. Remaining: AGT gateway, sandboxed supervisor, reference skills.

**Features:**

- [x] `design/mcp-client.md` — client lifecycle, transport choice (stdio vs HTTP), tool discovery. Rewritten in PR #49 to match the canonical schema shape.
- [x] `feat(mcp): MCPClientManager + mcp_tool skill execution` — stdio + SSE transports, lazy + eager session startup, tool-schema caching. PR #45.
- [x] `feat(mcp): MCP server registry in workspace.yaml` — `mcp_servers:` array with `id`, `transport`, `command`, `endpoint`, `env`, `credentials_ref`, `sandboxed`. PR #47, fixed in PR #49 (schema↔runtime shape alignment).
- [x] `fix(mcp): schema↔runtime alignment + hello-world example` — `parse_mcp_servers` consumes the canonical schema shape; `inputSchema` forwarded to LLM tool definitions; compile-time missing-server guard; `cwd=workspace_root` for stdio servers. PR #49.
- [x] `feat(cli): swarmkit author mcp-server` — conversational MCP server authoring. Landed alongside M5 MCP work.
- [x] `design/details/knowledge-curator.md` — Knowledge Curator topology design. PR #46. KB architecture: dedicated curator topology, workers read-only, integration via Qdrant/Kreuzberg/Notion MCP servers.
- [x] `design/details/skill-registry.md` — community skill import + discovery design. Covers Agent Skills (SKILL.md) + MCP ecosystems.
- [ ] `feat(mcp): MCP calls gated through GovernanceProvider` — every `call_tool` goes through `evaluate_action` before execution (§18.1). Blocked on M2 AGT integration for real enforcement; MockGovernanceProvider pass-through is wired.
- [ ] `feat(mcp): sandboxed server supervisor` — Docker-based, matches §8.8 sandboxing requirement. `sandboxed: true` is accepted by the schema but ignored at runtime.
- [ ] `feat(skills): github-repo-read reference capability skill` — wraps a public MCP server (e.g. `@modelcontextprotocol/server-github`).
- [ ] `feat(skills): slack-notify reference capability skill` — wraps Slack MCP (or local mock).
- [ ] `design/details/knowledge-mcp-server.md` — **task #25.** Spec an MCP server that exposes the SwarmKit corpus live. Implementation can fold into this milestone or slip to M6.

**Exit demo:** topology reads a public GitHub repo via MCP, passes the diff to a judge, writes result to audit. Kill the MCP server mid-run; runtime reports the failure gracefully through the policy-engine failure path.

## Milestone 6 — Reference topology: Code Review Swarm

**Goal:** the canonical Cisco-style multi-leader swarm, production-quality, shippable.

**Design reference:** §11.1, §4.2.

**Features:**

- [ ] `design/topology-code-review.md` — full agent tree, skill map, HITL gates.
- [ ] `feat(reference): code-review-swarm topology.yaml` — three leaders, workers, guarded channels.
- [ ] `feat(reference): 5–7 archetypes needed by the topology` — one design note per archetype cluster.
- [ ] `feat(reference): 10–12 skills needed by the topology` — spread across design notes.
- [ ] `feat(cli): github webhook handler for trigger` — kicks the swarm on PR open.
- [ ] `test(reference): golden-path PR review end-to-end` — fixture PR, deterministic review output.

**Exit demo:** `just demo-code-review` — a fixture PR goes in; engineering, QA, and ops leaders coordinate; final deploy step pauses for HITL approval; approving the review queue item releases the deploy.

## Milestone 7 — Reference topology: Skill Authoring Swarm

**Goal:** users can author new skills through terminal chat.

**Design reference:** §11.1, §12.

**Features:**

- [ ] `design/topology-skill-authoring.md` — agent roles, conversation flow, test-execution model.
- [ ] `feat(reference): skill-authoring-swarm topology.yaml`.
- [ ] `feat(reference): archetypes for conversation-leader, schema-drafter, test-execution-leader, publication-worker`.
- [ ] `feat(cli): swarmkit author skill [name]` — launches the topology in chat mode.
- [ ] `feat(runtime): authoring-provenance tagging` — swarm-authored skills get `authored_by_swarm`, locked out of production use until human review.
- [ ] `test(reference): author a skill end-to-end` — scripted conversation produces a valid skill file.

**Exit demo:** `swarmkit author skill` — live conversation produces a new skill YAML, runs its test case against a real MCP server, and publishes to the workspace on user approval. Design doc's 10-minute first-extension promise (§3.4) validated.

## Milestone 8 — Reference topology: Workspace Authoring Swarm

**Goal:** `swarmkit init` in an empty directory produces a working workspace through conversation. This is the v1.0 non-developer on-ramp.

**Design reference:** §11.1, §14.2.

**Features:**

- [ ] `design/topology-workspace-authoring.md` — conversation flow, use-case detection, scaffold templates.
- [ ] `feat(reference): workspace-authoring-swarm topology.yaml`.
- [ ] `feat(cli): swarmkit init` — launches in empty directory.
- [ ] `feat(runtime): workspace scaffold generator` — writes topology + archetypes + skills + workspace.yaml based on conversation outputs.
- [ ] **Open design question — task #26:** does the Workspace Authoring Swarm stay interactive past `init`? The analyst path benefits from a "keep going" mode where the same conversation can add/modify agents after the initial scaffold. Decide during this milestone's design note.

**Exit demo:** `mkdir my-swarm && cd my-swarm && swarmkit init` — user answers a few questions; a runnable workspace exists at the end; `swarmkit run` executes it. Design doc's 15-minute first-run promise (§3.4) validated. A first-time analyst (no prior SwarmKit context) completes this without reading the design doc.

## Milestone 9 — Eject, HTTP server, scheduled mode

**Goal:** the other two execution modes from §14.1 + the eject escape hatch.

**Design reference:** §14.1, §14.4.

**Features:**

- [ ] `design/eject.md` — generated project structure, dependency pinning, README template.
- [ ] `feat(runtime): swarmkit eject <topology>` — writes `./generated/` with standalone LangGraph code.
- [ ] `feat(runtime): FastAPI HTTP server for persistent mode`.
- [ ] `feat(runtime): scheduler (cron, webhook, file_watch)`.
- [ ] `test(runtime): ejected code runs without swarmkit installed` — CI step.

**Exit demo:** eject the code-review swarm, install only its `requirements.txt` in a fresh venv, run it. No SwarmKit dependency. Output matches the in-framework run.

## Milestone 10 — Catalogue polish + launch prep

**Goal:** ship-ready.

**Features:**

- [ ] remaining archetypes to hit ~15 (§13.1 list).
- [ ] remaining skills to hit ~20.
- [ ] documentation site (MkDocs or Docusaurus — decision in a design PR).
- [ ] Docker image build + publish workflow.
- [ ] PyPI + npm publish workflows with trusted publishing.
- [ ] v1.0 release notes.

**Exit demo:** public launch post on GitHub Discussions + Discord announcement; a first user can `pip install swarmkit`, `swarmkit init`, and have a working swarm in <15 min.

---

## Open questions that block specific milestones

| §21 question | Blocks |
|-|-|
| Schema canonical format (YAML/JSON) | M0 |
| AGT version pinning strategy | M2 |
| Policy language (YAML/Rego/Cedar) default | M2 |
| Sandboxing requirement for generated MCP servers | M5 |
| Governance overhead target (10–20%) verification | M4 (exit demo must measure) |

Resolve each in a `design/decision-<slug>.md` PR before the blocked milestone starts.
