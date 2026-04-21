# Implementation Plan — SwarmKit v1.0

**Source of truth:** `design/SwarmKit-Design-v0.6.extracted.md` (§20.1 lists the Phase 1 scope). This plan decomposes that scope into milestones and features, each of which becomes one or more PRs under the [feature delivery workflow](../CLAUDE.md#feature-delivery-workflow--mandatory).

**Status:** drafted 2026-04-21. Living document — update as milestones land.

## How this plan works

- **Milestones** are coarse checkpoints. Each milestone has an **exit criterion** — something demonstrable a human can watch.
- Each milestone contains a set of **features**. One feature = one design note at `design/details/<slug>.md` + one implementation PR (or a design PR followed by an implementation PR for larger features).
- Milestones are mostly sequential but features within a milestone often parallelise. Dependencies are noted per-feature.
- Effort estimates below mirror §20.1 (Phase 1: 13–16 weeks). Not commitments — they inform ordering, not a schedule.

## Milestone overview

| # | Milestone | Exit demo |
|---|-----------|-----------|
| 0 | Schemas nailed down | Load every v0.6 example in the design doc; valid in both Python and TS validators. |
| 1 | Topology loading & resolution | `swarmkit validate path/to/topology.yaml` prints a resolved tree; archetype and skill refs resolved. |
| 2 | `GovernanceProvider` abstraction + AGT Tier 1 | Policy decisions roundtrip through `AGTGovernanceProvider` for a real scope check; mock provider used in unit tests. |
| 2.5 | `ModelProvider` abstraction | Topology with two agents on different providers (e.g. Anthropic leader + Ollama worker) loads, `just demo-model-providers` prints green per installed provider. |
| 3 | LangGraph compiler — capability + coordination | `swarmkit run hello-topology.yaml` executes a two-agent swarm and prints the final state. |
| 4 | Decision + persistence skills | Same topology, now with an LLM judge and an audit-log write; audit entries survive process restart. |
| 5 | MCP integration | Topology calls a real MCP server (filesystem or a simple HTTP tool); AGT security gateway wraps the call. |
| 6 | Reference: Code Review Swarm | `just demo-code-review` runs the full swarm against a fake PR and produces a review verdict + HITL gate. |
| 7 | Reference: Skill Authoring Swarm | `swarmkit author skill` launches a conversational chat that produces a tested, published skill. |
| 8 | Reference: Workspace Authoring Swarm | `swarmkit init` in an empty dir produces a working workspace through conversation. |
| 9 | Eject + HTTP server + scheduled mode | `swarmkit eject` produces runnable LangGraph code; `swarmkit serve` accepts HTTP triggers. |
| 10 | Catalogue polish + launch prep | ~15 archetypes, ~20 skills, docs site, Docker image, PyPI publish dry-run. |

## Cross-cutting workstreams

Run in parallel with the milestones above:

- **Docs:** concept pages for topology / agents / skills / archetypes / governance land as soon as their milestone does. One docs PR per concept.
- **CI:** GitHub Actions pipeline (lint + typecheck + test on push/PR; matrix on py 3.11, 3.12, 3.13) — first PR, before milestone 1. Design note at `design/details/ci-pipeline.md`.
- **Packaging:** PyPI + npm + Docker publish workflows finalised in milestone 10. Trial runs in milestone 5+.
- **Governance hardening:** every milestone that touches `governance/` is reviewed against §8 Separation of Powers invariants.

---

## Milestone 0 — Schemas

**Goal:** every artifact example in the v0.6 design doc (topology, skill, archetype, workspace, trigger) validates cleanly in both the Python and TS validator. Codegen Pydantic models + TS types from the schemas. This is the foundation — nothing else is worth building until the schemas are firm.

**Design reference:** §6.3, §10, §13, §9.3.

**Features:**

- [ ] `design/topology-schema-v1.md` — promote the v0.6 sketch to a detailed spec (required fields, constraint rules, extensibility).
- [ ] `design/skill-schema-v1.md` — finalise the four-category discriminator, composition, provenance.
- [ ] `design/archetype-schema-v1.md` — abstract-skill placeholders (§6.6 edge case).
- [ ] `design/workspace-schema-v1.md` — governance provider selection, identity provider, storage config.
- [ ] `design/trigger-schema-v1.md` — cron, webhook, file_watch, manual.
- [ ] `feat(schema): pydantic model codegen from JSON Schema` — generated models live in `swarmkit_schema.models`.
- [ ] `feat(schema): typescript type codegen from JSON Schema` — generated types in `@swarmkit/schema/types`.
- [ ] `test(schema): round-trip every v0.6 example` — fixtures lifted verbatim from the design doc.

**Exit demo:** `just demo-schema` loads every example artifact and prints a green validation report.

## Milestone 1 — Topology loading & resolution

**Goal:** given a workspace directory, load and fully resolve every topology, archetype, and skill file. Resolved topology is a typed Pydantic model; CLI can print it.

**Design reference:** §10, §14.3 (steps 1–3).

**Features:**

- [ ] `feat(runtime): workspace directory loader` — enumerates topologies/, archetypes/, skills/; reports conflicts.
- [ ] `feat(runtime): archetype resolver` — merges archetype defaults into agent definitions with overrides.
- [ ] `feat(runtime): skill resolver` — validates each referenced skill, resolves composed skills.
- [ ] `feat(runtime): ResolvedTopology data model` — frozen Pydantic model that the compiler consumes.
- [ ] `feat(cli): swarmkit validate <path>` — prints resolution report; non-zero exit on failure.
- [ ] `test(runtime): resolve every reference/ artifact` — covers at least the three v1.0 topologies once their YAML lands.

**Exit demo:** `swarmkit validate examples/hello-swarm/workspace/` prints a resolved tree with all archetype/skill refs expanded.

## Milestone 2 — GovernanceProvider + AGT Tier 1

**Goal:** lock in the abstraction; wire AGT's Agent OS policy engine for deterministic Tier 1 checks; introduce a `MockGovernanceProvider` for tests.

**Design reference:** §8.5, §8.6 Tier 1, §16.2, §16.3.

**Features:**

- [ ] `design/governance-provider-interface.md` — finalise method signatures, error shapes, async semantics.
- [ ] `feat(governance): AGTGovernanceProvider policy evaluation` — wraps agent-os-kernel for scope checks.
- [ ] `feat(governance): AGTGovernanceProvider audit` — wraps Agent SRE for append-only event recording.
- [ ] `feat(governance): MockGovernanceProvider` — deterministic, assertable, test-only.
- [ ] `feat(runtime): middleware pipeline for skill invocation` — every skill call routes through `evaluate_action` before execution.
- [ ] `test(governance): separation-of-powers invariants` — executive cannot modify audit, cannot bypass policy.

**Exit demo:** a unit-test swarm where a worker tries to invoke a skill it lacks the scope for; policy denies; audit records the attempt; test asserts both.

## Milestone 2.5 — Model provider abstraction

**Goal:** topology YAML picks an LLM provider per agent (Anthropic, OpenAI, Google, Ollama, custom); the runtime dispatches through a `ModelProvider` interface with built-in implementations and a plugin path. Mirror of `GovernanceProvider`. Blocks M3 because the compiler needs a dispatch seam.

**Design reference:** `design/details/model-provider-abstraction.md`; mirrors §8.5.

**Features:**

- [x] `design/details/model-provider-abstraction.md` — ABC, built-in providers, registration, credentials contract.
- [ ] `feat(runtime): ModelProvider ABC + MockModelProvider + AnthropicModelProvider` — minimum to unblock M3.
- [ ] `feat(runtime): OpenAI + Google + Ollama providers` — three sibling PRs, each with recorded-response unit tests.
- [ ] `design/details/model-provider-tool-calling.md` — canonical tool-call format and per-provider translation (blocks M5).
- [ ] `feat(runtime): provider registry + entry-point discovery + workspace.yaml overrides`.
- [ ] `test(runtime): topology with a non-registered provider fails load with a clear error`.

**Exit demo:** `just demo-model-providers` — a minimal completion through each installed provider; missing SDKs / creds report `skipped` (not failed). `examples/model-choice/` — two-agent topology with a Claude leader and an Ollama worker; one YAML, one README.

## Milestone 3 — LangGraph compiler (capability + coordination)

**Goal:** topology → `StateGraph` for the simple cases: capability skills (straight-line tool calls) and coordination skills (parent→child, peer→peer A2A). Checkpointing to SQLite.

**Design reference:** §14.3, §14.5, §5.3.

**Features:**

- [ ] `design/langgraph-compiler.md` — full translation rules with diagrams.
- [ ] `feat(compiler): node construction from agent` — one LangGraph node per agent, hooked to governance middleware.
- [ ] `feat(compiler): edge construction from hierarchy` — parent/child edges, guarded cross-zone channels flagged.
- [ ] `feat(compiler): coordination skill dispatch` — A2A handoff skill → StateGraph edge.
- [ ] `feat(compiler): capability skill dispatch` — MCP tool call wrapper (mock MCP in this milestone).
- [ ] `feat(runtime): SQLite checkpointer wiring` — per-topology checkpoint file under `.swarmkit/state/`.
- [ ] `feat(cli): swarmkit run <topology>` — one-shot execution (§14.1).

**Exit demo:** two-agent hello-world topology runs end-to-end: root agent hands off to a worker, worker echoes input with a transform, root returns final state. Checkpoint file persists; re-run with `--resume` picks up state.

## Milestone 4 — Decision + persistence skills

**Goal:** LLM judge skills (Tier 2), deterministic validator skills, audit-log writes, review-queue primitive, skill-gap log primitive.

**Design reference:** §6.2, §8.6 Tier 2/3, §12.1, §14.5, §17.

**Features:**

- [ ] `design/decision-skills.md` — how verdicts and confidence scores flow through state.
- [ ] `feat(skills): llm-judge primitive skill` — uses Anthropic SDK; rubric-driven; returns verdict+confidence.
- [ ] `feat(skills): schema-validator primitive skill` — deterministic, jsonschema-backed.
- [ ] `feat(skills): multi-persona panel composition (Tier 3)` — fan-out + consensus.
- [ ] `feat(runtime): review queue primitive` — file-backed in v1.0, pluggable storage.
- [ ] `feat(runtime): skill gap log primitive` — automatic entry when HITL thresholds are crossed (§12.1).
- [ ] `feat(governance): AGT trust scoring integration` — trust-score decay on repeated judicial escalations.

**Exit demo:** extend the hello-world topology: worker output goes through a Tier 2 LLM judge; low-confidence verdicts land in the review queue; one failing run appears in the skill gap log.

## Milestone 5 — MCP integration

**Goal:** real MCP servers power capability skills; AGT security gateway wraps every MCP call.

**Design reference:** §18.

**Features:**

- [ ] `design/mcp-client.md` — client lifecycle, transport choice (stdio vs HTTP), tool discovery.
- [ ] `feat(mcp): MCP client with AGT security gateway` — every call gated through policy engine (§18.1).
- [ ] `feat(mcp): MCP server registry` — declarative registration in workspace.yaml.
- [ ] `feat(mcp): sandboxed server supervisor` — Docker-based, matches §8.8 sandboxing requirement.
- [ ] `feat(skills): github-repo-read reference capability skill` — wraps a public MCP server.
- [ ] `feat(skills): slack-notify reference capability skill` — wraps Slack MCP (or local mock).

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

**Exit demo:** `mkdir my-swarm && cd my-swarm && swarmkit init` — user answers a few questions; a runnable workspace exists at the end; `swarmkit run` executes it. Design doc's 15-minute first-run promise (§3.4) validated.

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
