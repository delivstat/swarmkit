PRODUCT & ARCHITECTURE DESIGN
SwarmKit
An open-source framework for composing, running,
and growing multi-agent swarms.
Version 0.6 — Draft for Review
Author: Srijith
Date: April 2026


## [Heading1] Changelog from v0.5
This version addresses a gap in v1.0's first-run experience for non-developer users. v0.5's release plan deferred the visual UI to v1.1, which meant v1.0 users had no guided authoring path — they would need to hand-write YAML files. v0.6 closes that gap by adding a Workspace Authoring Swarm as a third v1.0 reference topology. New users can now bootstrap their workspace through a conversational CLI interface from day one.

## [Heading3] 1. Workspace Authoring Swarm as a third v1.0 reference topology
v0.5 shipped two v1.0 reference topologies (Code Review Swarm, Skill Authoring Swarm). v0.6 adds a third — Workspace Authoring Swarm — which bootstraps a new user's entire workspace from a conversation. This extends the conversational authoring pattern established by Skill Authoring Swarm to workspace-level artifacts (topologies, archetypes, workspace config). The framework's extensibility story becomes more complete: skills, archetypes, topologies, and entire workspaces are all conversationally authored through swarms that are themselves SwarmKit topologies.

## [Heading3] 2. Authoring CLI entry points
v0.6 adds explicit CLI entry points for conversational authoring: swarmkit init (workspace authoring), swarmkit author topology, swarmkit author skill, swarmkit author archetype. Each entry point launches the relevant authoring swarm in terminal chat mode. Advanced users who prefer direct YAML editing are unaffected — both paths exist and produce identical artifacts.

## [Heading3] 3. Phase 1 effort adjusted to 13-16 weeks
Adding the Workspace Authoring Swarm costs approximately 2-3 weeks of additional Phase 1 work. Phase 1 effort grows from 11-13 weeks in v0.5 to 13-16 weeks in v0.6. This is a deliberate trade: v1.0 becomes meaningfully more usable for non-developers, and the v1.1 UI becomes a presentation layer on top of existing authoring swarms rather than new infrastructure.

## [Heading3] 4. Clarified UI deferral rationale
v0.5 deferred the UI to v1.1 without fully acknowledging the non-developer adoption cost. v0.6 reframes the deferral: the visual UI is deferred because conversational authoring through CLI is sufficient for v1.0, not because non-developer support is deferred. The UI when it ships in v1.1 becomes a nicer front-end to the same authoring swarms, not a new mechanism users must relearn.

## [Heading2] Prior changelog — v0.4 to v0.5

## [Heading3] 1. Microsoft AGT adoption
v0.4 positioned SwarmKit as building its own policy engine, identity model, observability infrastructure, and compliance mapping. v0.5 changes that: we adopt AGT's seven-package system as the backbone for judicial (Agent OS policy engine, sub-millisecond p99), executive constraints (Agent Runtime sandboxing, Agent Mesh identity), and media (Agent SRE telemetry, Agent Compliance evidence). The Separation of Powers conceptual model remains entirely — we now have best-in-class implementations of the underlying capabilities rather than needing to build them. This saves meaningful Phase 1 scope and produces a more credible governance story than any solo-built alternative could.

## [Heading3] 2. GovernanceProvider abstraction layer
To avoid vendor lock-in to AGT specifically, SwarmKit introduces a GovernanceProvider abstraction — a narrow, stable interface covering policy evaluation, identity verification, and event recording. AGT is the v1.0 implementation. If AGT is deprecated, forked, or if a competing toolkit emerges, a new GovernanceProvider implementation replaces it without changes to the topology schema, runtime, or other components. This is the same pattern Terraform uses for cloud providers — narrow interface, multiple implementations, genuine portability at a stable boundary.

## [Heading3] 3. Execution engine remains LangGraph-native
After thorough investigation, v0.5 is explicit that v1.0 does not attempt to abstract over the execution engine. LangGraph's surface (StateGraph, channels, reducers, Pregel execution, checkpointing) is too broad and evolving to wrap cleanly. The topology-as-data schema is already the portability layer — if a future version needs to target Microsoft Agent Framework or another engine, a second runtime compiler is written rather than a thin wrapper. The eject command remains the user-facing escape hatch.

## [Heading3] 4. Phase 1 scope and effort adjusted
With AGT handling policy engine, identity, sandboxing, telemetry, and compliance mapping, Phase 1 effort drops from 14-16 weeks in v0.4 to 11-13 weeks in v0.5. What remains SwarmKit-specific is the topology schema, the skill and archetype abstractions, the conversational authoring, the reference topologies, and the LangGraph integration. This is materially less infrastructure and materially more distinctive value.

## [Heading2] Prior changelog — v0.3 to v0.4

## [Heading3] 1. The Separation of Powers model
v0.3 stated that swarm evolution required human approval but did not explain what structurally enforced this. v0.4 introduces a four-pillar governance model — legislative, executive, judicial, media — modelled on the separation of powers principle from real-world governance. Each pillar has a distinct role, operates behind its own process or module boundary, and cannot be overridden by agents regardless of prompt engineering. This replaces the earlier defensive framing (preventing misbehaviour) with a structural framing (making misbehaviour structurally impossible in the common case and visible in edge cases).

## [Heading3] 2. MCP server authoring as a bounded capability
v0.3 left open whether swarms could author their own MCP servers or only skills that wrap existing servers. v0.4 resolves this: MCP server authoring is a supported capability through the same authoring pattern as skills, with the same human-review gate. The Skill Authoring Swarm surfaces the limitation when no suitable server exists and offers the human two paths — scaffold-and-review, or delegate-to-human. This closes a conceptual loop while keeping governance intact.

## [Heading3] 3. Judicial tiering for token economy
The separation of powers model adds a judicial layer that evaluates agent outputs. v0.4 addresses the legitimate concern that naive implementation would multiply token costs significantly. The judicial layer supports three tiers (deterministic validation, single LLM judge, multi-persona panel) with explicit routing rules that default to the cheapest sufficient tier. The framework targets 10-20% governance overhead in typical use, not 300-400%.

## [Heading3] 4. Logical separation for v1.0, hardening for v2.0
v0.4 is explicit that v1.0 ships with the four pillars logically separated (distinct modules, identities, and storage boundaries) but running in a single process for implementation simplicity. Full process-level separation is a v2.0 hardening target. The schema and architecture are designed for true separation from day one; the first implementation is lower-overhead.

## [Heading2] Prior changelog — v0.2 to v0.3

## [Heading3] 1. Skills vs archetypes distinction
v0.2 introduced both concepts but did not explicitly contrast them, leaving readers to infer the difference from context. v0.3 added a dedicated subsection (6.6) framing skills as verbs (what an agent does) and archetypes as nouns (what an agent is). Examples were added showing the same capability at both levels to make the distinction concrete.

## [Heading3] 2. Archetype provenance
v0.3 extended provenance to archetypes, matching the treatment introduced for skills in v0.2.

## [Heading3] 3. Explicit composition hierarchy
v0.3 made explicit the three-layer composition: topologies contain agents, agents are instantiated from archetypes, agents invoke skills.

## [Heading2] Prior changelog — v0.1 to v0.2

## [Heading3] 1. Product positioning shift
v0.1 framed SwarmKit as a framework for composing swarms. v0.2 frames it as a framework for composing and growing swarms. Swarm evolution — the mechanism by which swarms identify their own capability gaps and extend themselves through human-approved skill authoring — becomes a first-class concern rather than a future feature. This changes the product positioning, the reference topology list, and the architectural depth of the skills system.

## [Heading3] 2. Skills as universal extension primitive
v0.1 treated MCP tools, validation gates, A2A handoffs, evaluation criteria, and notification channels as separate schema concepts with their own configuration. v0.2 unifies all of these as skills with different category semantics. This significantly simplifies the schema, matches OpenClaw's proven mental model structurally, and gives the community a single extension surface to contribute to.

## [Heading3] 3. Rynko Flow decoupling
v0.1 treated Rynko Flow integration as an optional but elevated concern with dedicated schema fields and a strategic relationship section. v0.2 removes all framework-level coupling to Rynko Flow. Rynko Flow becomes one of many MCP-exposed validation providers, accessed through the same mechanism as any other MCP tool. This produces cleaner framework positioning, genuine vendor neutrality, and ironically a stronger story for Rynko Flow itself — adoption is earned on merit rather than engineered through coupling.

## [Heading3] 4. Conversational skill authoring
v0.1 did not address skill authoring explicitly. v0.2 introduces a Skill Authoring Swarm as one of the two v1.0 reference topologies. Users describe skills in natural language through a conversational interface; the authoring swarm asks clarifying questions, produces structured previews, runs mandatory tests, and publishes only on successful validation. The authoring tool is itself a SwarmKit topology — the framework eats its own dogfood.

## [Heading3] 5. Two reference topologies for v1.0
v0.1 shipped one reference topology (Code Review Swarm) in v1.0. v0.2 ships two: Code Review Swarm demonstrates multi-agent coordination, and Skill Authoring Swarm demonstrates extensibility. Together they tell a launch story — structured coordination plus natural growth — that neither alone could carry.


## [Heading1] Contents
1.  Executive Summary
2.  Problem Statement & Origin
3.  Product Vision
4.  Target Users & Use Cases
5.  Core Concepts & Mental Model
6.  Skills — The Universal Extension Primitive
7.  Architectural Principles
8.  The Separation of Powers Model
9.  System Architecture
10.  Topology Schema (High-Level)
11.  The Reference Topology Library
12.  Skill Authoring & Swarm Evolution
13.  Archetype Library
14.  Runtime Architecture
15.  User Interface
16.  Identity, Access & Governance
17.  Quality Evaluation
18.  MCP Integration & External Tools
19.  Comparison to Existing Tools
20.  Release Plan
21.  Open Questions for Review


## [Heading1] 1. Executive Summary
SwarmKit is an open-source framework for composing, running, and growing multi-agent AI swarms. It treats swarm topology — who exists, who reports to whom, what skills they can exercise — as declarative data rather than imperative code. This separation lets non-developers compose agent teams visually while developers retain full programmatic control.
The framework's distinctive feature is that swarms are not static compositions. Every swarm can observe its own capability gaps through usage patterns, surface those gaps for human review, and grow new skills through a conversational authoring interface. This mirrors how real engineering teams expand their capabilities. Skill authoring is itself implemented as a SwarmKit topology, which serves as both a reference example and a validation that the framework can handle complex agent workflows.
Governance is architected as a four-pillar Separation of Powers model — legislative, executive, judicial, media — implemented on top of the Microsoft Agent Governance Toolkit (AGT, released April 2026, MIT-licensed). Adopting AGT for policy enforcement, identity, sandboxing, and observability means SwarmKit delivers production-grade governance without reinventing it. A narrow GovernanceProvider abstraction keeps the framework portable across future governance toolkits.
The motivating reference is Cisco's April 2026 paper on agentic engineering. SwarmKit generalises the pattern into a reusable framework, layered on existing execution engines (LangGraph in v1.0). The product surface is a Python runtime with CLI and HTTP server, a web-based composer for visual topology design and skill authoring, and a topology specification that is portable, version-controllable, and shareable.
This document is the foundational design artifact (version 0.6). It captures the conceptual model, architectural decisions, and component-level design at a level sufficient for engineering scoping. Detailed design documents will follow once this design is reviewed and approved.
STATUS — This is a draft for review. Sections contain decisions that are open or contested and require explicit input before detailed design begins. Section 21 consolidates all open questions.

## [Heading1] 2. Problem Statement & Origin

## [Heading2] 2.1 The shift from single agents to coordinated swarms
AI agent tooling in 2025 and early 2026 matured rapidly around the single-agent pattern: one LLM, a system prompt, a set of tools, and a reasoning loop. Frameworks like LangChain, the OpenAI Agents SDK, and OpenClaw made building such agents straightforward. Tens of thousands of production deployments now use this pattern.
The frontier has moved beyond single agents. Production teams are increasingly deploying coordinated swarms — multiple specialised agents working together with structured handoffs, evaluation gates, and human review at boundary points. The Cisco engineering team's April 2026 paper documented a 93% reduction in time-to-root-cause for debugging and a 65% reduction in development workflow time using this pattern. Walmart, Siemens, DHL, and major banks have published similar architectural shifts.

## [Heading2] 2.2 Why existing tools fall short
LangGraph, CrewAI, AutoGen, and similar frameworks all support multi-agent coordination, but each requires writing the swarm topology as code. This creates four friction points:
Composition is engineering work. Defining who reports to whom, what they can access, and how they hand off requires writing Python. Non-engineering stakeholders cannot participate in swarm design even though they have essential domain knowledge.
Topologies are not portable. A swarm built in CrewAI cannot be lifted into LangGraph without a rewrite. Community sharing of swarm patterns is limited because there is no standard format.
Governance is bolt-on. Access control, audit logging, evaluation criteria, and human review queues are typically added per-swarm rather than being framework primitives.
Swarms cannot grow. Existing frameworks treat swarms as static compositions. When a swarm encounters a new capability requirement, the path forward is manual engineering work. There is no mechanism for a swarm to identify its own capability gaps and incorporate new capabilities through structured human-approved authoring.

## [Heading2] 2.3 The Cisco architecture as reference
The Cisco paper described a clean separation between worker agents (specialist executors) and leader agents (coordinators with shared visibility). Leaders communicate laterally via Google's A2A protocol; workers access tools via Anthropic's MCP protocol. State is checkpointed at every step. Evaluation gates and human review are first-class concerns.
That architecture works. What it lacked was reusability and growth — Cisco built it for their internal use case, with a fixed capability set defined at engineering time. SwarmKit takes the same conceptual model and makes it composable, portable, extensible, and accessible to teams without Cisco-scale engineering capacity.

## [Heading1] 3. Product Vision

## [Heading2] 3.1 What SwarmKit is
SwarmKit is the operating model for multi-agent swarms that grow. It is to LangGraph what Terraform is to AWS APIs — a higher-level declarative layer that makes the underlying capabilities accessible to a wider audience, while remaining transparent and inspectable for those who want to drop down.
The product is open source under a permissive licence. It targets developers and small teams initially, with enterprise concerns (multi-tenancy, SSO, organisation-wide governance) supported in the schema from day one but built incrementally.
CHANGED FROM v0.1 — v0.1 described SwarmKit as a framework for composing swarms. v0.2 repositions it as a framework for composing and growing swarms. Evolution is a first-class capability, not an afterthought.

## [Heading2] 3.2 The three pillars
SwarmKit's product story rests on three pillars that together differentiate it from every existing agent framework:
Composition through topology-as-data. Swarms are defined in YAML or JSON, not Python code. The same topology file runs on any compatible runtime, can be edited visually or in an IDE, and is version-controlled like any other configuration artifact.
Skills as the universal extension primitive. Every capability an agent can exercise — calling a tool, validating output, handing off to another agent, evaluating quality, sending a notification — is modelled as a skill. One extension mechanism, one contribution surface, one mental model.
Growth through human-approved authoring. Swarms observe their own capability gaps and surface them. A skill-authoring swarm, itself built on SwarmKit, turns natural-language descriptions into tested, validated, publishable skills. Humans approve every step; evolution is safe, auditable, and reversible.

## [Heading2] 3.3 What SwarmKit is not
SwarmKit is not an execution engine. It does not compete with LangGraph, CrewAI, or AutoGen. It uses them. v1.0 ships with a LangGraph-based runtime that dynamically constructs graph definitions from topology files.
SwarmKit is not a personal AI assistant. It does not compete with OpenClaw or similar single-agent personal automation tools. The smallest meaningful SwarmKit deployment has at least a leader and a few workers; the framework's value emerges from coordination, not from any single agent's capability.
SwarmKit is not a no-code tool in the marketing sense. Defining a useful swarm requires understanding what each agent should do and what counts as good output. The framework lowers the barrier from "write Python code" to "compose a structured definition and grow new skills as needed" — but it does not eliminate the need for thoughtful design.

## [Heading2] 3.4 The first-run promise
A new user should be able to clone the SwarmKit repository, install the runtime, and run a working swarm in under fifteen minutes. They should then be able to author a new skill for their swarm through a conversational interface, with the authoring swarm guiding them through every decision, in under ten additional minutes. That standard — working swarm plus easy extension on day one — is the bar for SwarmKit's first-run experience.
DESIGN NORTH STAR — Every architectural decision is evaluated against two criteria: does this make the first-run experience better, and does this make skill authoring easier? When in doubt, choose the path that produces working swarms faster and makes extension less intimidating.

## [Heading1] 4. Target Users & Use Cases

## [Heading2] 4.1 Primary user personas
Persona
Background
What they get from SwarmKit
Solo founder / indie developer
Building products that need agent capabilities. Comfortable with code but limited on time.
Pre-built reference topologies they can adapt. A path from prototype to production. Easy extension as their product grows.
Engineering team lead
Adopting agents in an established organisation. Needs to prove value while satisfying governance requirements.
Access control, audit logging, and human review built in. An architecture diagram they can show their security team, not a Python script.
AI infrastructure builder
Building agent capabilities for their organisation. Wants composability and reusability.
A skills library that grows over time. Patterns built once, shared internally, evolved through observed need.
Domain expert (non-engineer)
Subject matter expert who understands what good agent output looks like. Currently dependent on developers.
Visual composer that lets them define evaluation criteria and review workflows. Conversational skill authoring that lets them extend swarms without writing code.

## [Heading2] 4.2 The reference topologies
v1.0 ships with three fully working reference topologies. The remaining topologies ship as v1.x releases. Each demonstrates a distinct architectural pattern.

## [Heading3] Code Review Swarm (v1.0)
The canonical Cisco-style multi-leader workflow. Engineering Leader runs code generation and review workers. QA Leader runs test generation and execution. Operations Leader handles deployment with mandatory human approval. Demonstrates multi-leader coordination, A2A handoffs, validation gates, and tiered human-in-the-loop.

## [Heading3] Skill Authoring Swarm (v1.0)
A conversational swarm that authors new skills. User describes a desired capability in natural language; the swarm asks clarifying questions, drafts the skill definition, generates schemas, configures IAM scopes, runs validation tests against real MCP servers, and publishes only on successful test. Demonstrates the framework's extensibility story and serves as the primary mechanism for skill-level swarm evolution.

## [Heading3] Workspace Authoring Swarm (v1.0)
A conversational swarm that bootstraps a new workspace from scratch. User runs swarmkit init in an empty directory; the swarm asks about their use case, their data sources, their team size, and their compliance requirements, then produces a working workspace scaffold — topology files, starter skills, archetypes, and workspace config. Demonstrates that the conversational authoring pattern generalises from single artifacts to entire deployments. This is the primary v1.0 on-ramp for non-developer users.
CHANGED FROM v0.1 — v0.1 shipped one v1.0 topology. v0.2 added a second. v0.6 adds a third — Workspace Authoring Swarm — to address the non-developer on-ramp gap that would otherwise persist until v1.1 UI ships.

## [Heading3] Content Generation Swarm (v1.1)
Multi-modal generation with multi-persona evaluation and human-reviewed feedback loops. Strategy, Generation, QA, Publishing, and Analyst leaders. Demonstrates persistent knowledge bases and feedback contracts between leaders.

## [Heading3] Document Processing Swarm (v1.2)
High-volume parallel processing with validation-heavy gates. Intake, Extraction, Validation, and Output leaders. Demonstrates how validation skills (including but not limited to Rynko Flow) integrate into the swarm.

## [Heading3] Customer Support Swarm (v1.3)
Reactive query-driven swarm with classification and routing. Triage, Resolution, and Escalation leaders. Demonstrates RAG patterns and confidence-based human escalation.

## [Heading3] Codebase Knowledge Swarm (v1.4)
Knowledge-grounded advisory swarm with mandatory human review. Indexer, Q&A, and Change Proposal leaders. Demonstrates persistent knowledge bases that the swarm builds itself, and the always-HITL pattern.

## [Heading3] Home Assistant Swarm (v2.0 showcase)
Always-on, event-driven, multi-domain orchestration. Concierge Swarm coordinating across Vision, Smart Devices, News, Maintenance, and Family specialist swarms. Demonstrates long-running multi-swarm coordination.

## [Heading1] 5. Core Concepts & Mental Model

## [Heading2] 5.1 The four-level hierarchy
SwarmKit organises everything within a four-level hierarchy. Each level solves a specific problem and maps to a real-world boundary.
Level
Concept
Boundary
v1.0 Treatment
1
Organisation
Billing, identity, SSO, top-level governance
Implicit (single org per install)
2
Team
Access control, membership, role-based permissions
Implicit (single team per workspace)
3
Workspace
Deployment unit, shared resources, schedules, triggers
Explicit, fully implemented
4
Topology
Individual swarm definition
Explicit, fully implemented
Even though v1.0 collapses Organisation and Team into implicit single-tenant defaults, the schema and storage model accommodate the full hierarchy from day one. v2.0 multi-user collaboration can be added without breaking changes to existing topology files.

## [Heading2] 5.2 The agent hierarchy within a topology
Within any single topology, agents organise into a tree structure. The default UI view reflects a three-level structure — Root Orchestrator, Domain Leaders, and Workers — for clarity, but the schema supports arbitrary depth. A Leader can contain Sub-Leaders before reaching workers.
The constraint is structural: each agent has exactly one parent. No diamond inheritance. This keeps the topology a tree, makes ownership unambiguous, and keeps the visual representation tractable.

## [Heading2] 5.3 Communication patterns
Three communication patterns operate within and between agents, each with different trust implications. All three are implemented as skills (see Section 6):
Pattern
Where it applies
Trust treatment
Hierarchical
Parent to child within the same agent tree
Implicit trust within ownership boundary
Direct (intra-zone)
Workers under the same leader, in tight iteration loops
Allowed without gates; leader maintains full visibility
Guarded (cross-zone)
Pre-approved channels between workers under different leaders
Requires validation skill invocation; both leaders notified
A2A (peer leaders)
Leader-to-leader coordination across domains
Always passes through validation skill; receiving leader gets attested payload

## [Heading2] 5.4 First-class artifacts
SwarmKit treats certain runtime constructs as first-class entities:
Knowledge Bases — persistent, queryable stores that agents read from and write to over time. Accessed through persistence skills.
Review Queues — human-gated input pipelines. Agents write recommendations or flagged outputs into queues through persistence skills. Humans review and approve. Approved items flow to other agents or knowledge bases.
Audit Logs — configurable logging of agent actions, skill invocations, and decisions. Pluggable backends.
Skill Gap Logs — new in v0.2. Each leader maintains a structured log of observed capability gaps (patterns of requests that could not be served by existing skills, recurring HITL escalations, low-confidence outputs). These feed the Skill Authoring Swarm.
Schedules and Triggers — workspace-level rather than topology-level. The same swarm can be triggered by multiple schedules, and one schedule can trigger multiple swarms.

## [Heading1] 6. Skills — The Universal Extension Primitive
CHANGED FROM v0.1 — This entire section is new in v0.2. It replaces and unifies what v0.1 treated as separate concepts: MCP tool references, validation gates, A2A handoffs, evaluation configuration, and notification targets. All of these are now skills with different category semantics.

## [Heading2] 6.1 The skill abstraction
A skill is any discrete capability an agent can exercise. Calling an external tool, evaluating an output, handing off to a peer, writing to a knowledge base, sending a notification — each is a skill. Agents declare which skills they have access to; the runtime enforces that they cannot exercise skills they do not have; the topology schema references skills by ID rather than defining them inline.
This unification produces several benefits. The topology schema becomes dramatically simpler — one extension mechanism replaces five. The mental model for users becomes consistent — everything an agent can do is a skill, and skills are composable. The contribution surface for the community becomes unified — one registry, one authoring flow, one distribution mechanism. And the runtime treats all agent capabilities uniformly, with category-specific semantics applied as needed.

## [Heading2] 6.2 Skill categories
Skills share a common structure but differ in runtime semantics. Four categories are recognised by the framework:
Category
Purpose
Returns
Examples
Capability
Give the agent a new ability
Output data
MCP tool calls, retrieval, generation, API invocation
Decision
Let the agent evaluate or judge
Verdict with confidence
Validation gates, LLM judges, classification, business rules
Coordination
Let the agent communicate or hand off
Task status
A2A handoffs, escalations, notifications, worker spawning
Persistence
Let the agent remember or record
Write confirmation
Knowledge base writes, audit log entries, review queue items
To the user authoring a topology, all four categories are "skills" — the category determines how the runtime treats them, not how the user composes them.

## [Heading2] 6.3 Skill anatomy
A skill definition includes:
id: code-quality-reviewname: Code Quality Reviewcategory: decisiondescription: Evaluates a code diff for quality issues including SRP,              error handling, and naming conventions. Returns pass/fail              verdict with confidence score and per-criterion reasoning.inputs:  diff: { type: string, required: true }  language: { type: enum, values: [python, typescript, go], required: true }  outputs:  verdict: { type: enum, values: [pass, fail] }  confidence: { type: number, range: [0, 1] }  reasoning: { type: array, items: { criterion: string, verdict: string } }implementation:  type: mcp_tool  server: rynko_flow  tool: validate_code_review_v2  iam:  required_scopes: [repo:read]  constraints:  max_latency_ms: 2000  retry: { attempts: 2, backoff: exponential }  on_failure: escalate_to_humanprovenance:  authored_by: human  authored_date: 2026-04-15  version: 1.0.0

## [Heading2] 6.4 Provenance — a new property in v0.2
Every skill declares where it came from. This matters for trust, review, and governance. Recognised provenance values:
Value
Meaning
human
Hand-authored by a user. Fully owned by the authoring team.
authored_by_swarm
Produced by the Skill Authoring Swarm in response to an identified gap. Human-approved before publication.
derived_from_template
Generated from a template archetype with configuration. Partial human oversight.
imported_from_registry
Installed from a community registry. Trust depends on registry vetting.
vendor_published
Published by a commercial vendor. Trust depends on vendor relationship.
The runtime treats different provenance values with different default trust postures. Skills authored by a swarm, for example, default to requiring human approval on first execution against production data.

## [Heading2] 6.5 Skill composition
Skills can reference other skills. A multi-persona evaluation skill invokes several single-persona judge skills in parallel and combines their verdicts. A guarded cross-leader handoff invokes a validation skill followed by an A2A handoff skill. Composition is expressed in the skill definition and validated at topology load time.
This composition model means the framework needs only a small set of primitive skills. The sophisticated patterns (panels, pipelines, guarded channels) emerge from composing primitives, not from adding new schema concepts.

## [Heading2] 6.6 Skills versus archetypes
CHANGED FROM v0.1 — This subsection is new in v0.3. It makes explicit a distinction that v0.2 left readers to infer from context.
Skills and archetypes are both extension mechanisms, both versioned, both composable, both shareable through a registry. They are easy to confuse, but they operate at different layers and serve different purposes.
The sharpest way to hold the distinction:
A skill is a verb. Something an agent does. It has inputs, outputs, an implementation, and IAM requirements. It is invoked at runtime, not instantiated. Examples: call an MCP tool, evaluate an output, hand off to a peer, write to a knowledge base.
An archetype is a noun. A kind of agent. It has a role (root, leader, worker), a default model, a prompt template, a skill set, and default IAM. It is instantiated at topology-load time, not invoked. Examples: a supervisor leader, a code review worker, a content analyst worker.

## [Heading3] The three-layer composition hierarchy
SwarmKit has a three-layer composition model:
Layer
What it contains
Topology
A complete swarm definition. Contains agents organised in a hierarchy, plus artifacts (knowledge bases, review queues), schedules, triggers.
Agent
A single participant in a swarm. Instantiated from an archetype. Has a role, model, prompt, skill set, IAM scope, parent, and communication rules.
Skill
A single capability. Invoked by agents at runtime. Has inputs, outputs, an implementation, and scope requirements.
Each layer has a clear role and can be shared independently. A topology references archetypes to define its agents. An archetype references skills to define what its agents can do. Each layer can be versioned, distributed, and contributed to the community separately.

## [Heading3] An example showing both
Consider a worker that reviews code. The skills it invokes, each a separate shareable file:
- id: github-repo-read          # capability skill- id: eslint-analyse            # capability skill- id: code-quality-review       # decision skill- id: audit-log-write           # persistence skill
Each of these skills is independent. Each is used by other agents across many topologies. Each is authored, tested, and versioned separately.
The archetype that bundles them into a usable worker configuration:
id: code-review-workerrole: workerdefaults:  model:    provider: anthropic    name: claude-sonnet-4    temperature: 0.2      prompt:    system: |      You are a senior code reviewer specialising in Python       and TypeScript. Focus on correctness and maintainability.        skills:    - github-repo-read    - eslint-analyse    - code-quality-review    - audit-log-write      iam:    base_scope: [repo:read]    provenance:  authored_by: human  version: 1.2.0
A topology then instantiates this archetype — possibly overriding fields — to produce an actual agent:
- id: senior-code-reviewer  archetype: code-review-worker  parent: engineering-leader  # Overrides: this agent uses a different model  model:    name: claude-opus-4  # Adds a skill specific to this deployment  skills_additional:    - security-specific-review

## [Heading3] Why both layers exist
Skills exist independently of agents because they are shared across many agents. The github-repo-read skill is used by the code-review-worker, the code-gen-worker, the context-fetch-worker, and the debug-rca-worker. If skills were bundled inside agent definitions, every agent would carry its own copy — bad hygiene, painful to update, impossible to version centrally.
Archetypes exist independently of topologies because agent configurations are shared across many topologies. A supervisor-leader archetype is used in Code Review Swarm, Content Swarm, and Support Swarm. Each topology wants the same starting configuration with different specialisations. Without archetypes, every topology would reinvent the leader pattern.
So skills share atomic capabilities across agents. Archetypes share agent configurations across topologies. Different composition boundaries, different purposes.

## [Heading3] Archetype provenance
Archetypes carry provenance on the same basis as skills. The recognised values are the same (human, authored_by_swarm, derived_from_template, imported_from_registry, vendor_published) and the runtime applies the same trust defaults. An archetype authored by a swarm requires human review before being used to instantiate production agents.

## [Heading3] Edge cases worth knowing
Can a skill invoke another skill? Yes. Composition skills are an explicit pattern — a multi-persona evaluation skill invokes several single-judge skills. The topology sees one skill invocation; the skill internally dispatches.
Can an archetype reference an abstract skill placeholder? Yes. An archetype can declare "this agent needs a skill of category 'decision' with capability 'content_review'" without specifying which concrete skill. The topology then supplies the actual skill at instantiation. This makes archetypes more reusable across different validation backends.
Is the Skill Authoring Swarm an archetype or a topology? A topology. It contains multiple agents (Conversation Leader, specification workers, Review Leader, Test Execution Leader). Each of those agents is instantiated from an archetype. And each of those agents invokes skills at runtime. All three layers in play.

## [Heading1] 7. Architectural Principles
These principles guide every detailed design decision. When trade-offs arise, return to these as the tie-breakers.

## [Heading3] Topology as data, not code
A swarm definition is a YAML or JSON file. It is human-readable, version-controllable, diff-able in code review, shareable as a gist, and editable in any text editor. The runtime interprets this file at execution time. Code generation is rejected as an alternative because generated code creates a translation surface that drifts.

## [Heading3] Skills as the only extension primitive
When users want to add a capability, modify a behaviour, or integrate an external service, the answer is always "build or install a skill." There is no secondary extension mechanism. This keeps the framework comprehensible and the community's contribution surface unified.

## [Heading3] Framework-aligned, not framework-locked
v1.0 uses LangGraph as the underlying execution engine. The runtime constructs LangGraph graphs dynamically from topology definitions. Future versions may add adapters for other engines without breaking changes to the topology schema.

## [Heading3] Trust boundaries as first-class concept
Communication patterns are categorised by trust zone. Direct communication is allowed within zones. Cross-zone communication requires explicit validation skills. This makes the architecture's security posture visible in the topology file itself.

## [Heading3] Governance built in, not bolted on
Access control, audit logging, evaluation gates, and human review are framework primitives, not optional add-ons. The framework makes the right thing easy and the wrong thing visible.

## [Heading3] Growth through human-approved authoring
Swarms can identify their own capability gaps and author new skills to address them. Every step of this process is human-approved. There is no autonomous self-modification, no unsupervised learning loop. The framework observes, surfaces, and proposes; humans decide.

## [Heading3] Ergonomics determine adoption
Following OpenClaw's example, the first-run experience matters more than feature breadth. Ship reference topologies that work immediately. Make skill authoring conversational and forgiving. Document by example, not by abstraction.

## [Heading3] Eject, never lock in
At any point, a user must be able to export the LangGraph code that the runtime would execute, take ownership of it, and run it independently of SwarmKit.

## [Heading1] 8. The Separation of Powers Model
CHANGED FROM v0.1 — This entire section is new in v0.4. It replaces the lighter governance treatment of v0.3 with a structural model based on the separation-of-powers principle from real-world governance. The key claim is that governance must be architecturally enforced, not prompt-suggested.

## [Heading2] 8.1 The problem this solves
The framework has, up to this point in the document, asserted that swarms grow safely because human approval is required at key points — skill activation, MCP server deployment, production writes. But a governance model that says "require human approval" does not, by itself, explain what structurally prevents agents from bypassing that requirement. If enforcement is achieved only through prompt instruction, a sufficiently adversarial prompt, an injection attack, or a model hallucination could cause an agent to attempt unauthorised actions.
Real-world governance systems solve this problem not by making each actor incorruptible, but by structuring the system so that corruption in one component is exposed and constrained by others. Separation of powers — judicial, legislative, executive, with a free media surfacing information — is the canonical example. No single branch can unilaterally act without the others. The architecture itself enforces accountability.
SwarmKit adopts this pattern as its core governance architecture.

## [Heading2] 8.2 The four pillars
SwarmKit governance rests on four pillars, each with distinct responsibility, distinct authority, and distinct runtime boundary. No pillar can unilaterally act on behalf of another. The table below maps the classical governance concepts to their SwarmKit equivalents.
Pillar
Role
Who
What it cannot do
Legislative
Sets the rules
Topology files, IAM policies, scope definitions — written by humans, loaded at runtime
Cannot execute work. Cannot modify itself at runtime.
Executive
Does the work within the rules
All agents — roots, leaders, workers
Cannot modify the legislative layer. Cannot modify IAM policies. Cannot override judicial or audit.
Judicial
Evaluates whether actions conform to rules
Evaluation skills, validation gates, judges — logically separated from the executive
Cannot initiate work. Cannot modify itself. Cannot be suppressed by agents under evaluation.
Media
Surfaces information to humans
Audit log, observability dashboards, review queues, skill gap logs
Cannot modify past records. Cannot be edited by agents. Append-only from executive's perspective.

## [Heading2] 8.3 Pillar responsibilities in detail

## [Heading3] Legislative pillar
The legislative pillar is the set of configuration artifacts that define what is allowed: topology YAML files, IAM policy definitions, scope mappings, and archetype constraints. These are static artifacts loaded at runtime. The legislative pillar does not execute code or consume tokens. Its authority is derived from the fact that humans wrote these artifacts and agents cannot modify them.
Modifications to the legislative layer require human identity. A human operator editing a topology file, running `swarmkit apply`, or modifying IAM scopes through the UI is performing a legislative action. No agent, regardless of scope, can perform these actions at runtime. The framework's runtime does not accept legislative-layer modification requests signed by agent identities.

## [Heading3] Executive pillar
The executive pillar is the agents themselves. Roots, leaders, and workers — every agent defined in a topology operates as part of the executive. They have the authority to invoke skills, communicate with peers, write to knowledge bases, and trigger handoffs — within the bounds the legislative layer has defined.
An executive agent cannot grant itself new scopes. It cannot modify the topology it exists within. It cannot disable the judicial evaluators that review its output. It cannot suppress the media layer's recording of its actions. Its authority is bounded, and the bounds are enforced by the other pillars, not by the agent's own restraint.

## [Heading3] Judicial pillar
The judicial pillar evaluates executive actions against the rules set by the legislative layer. This includes validation gates, LLM judges, business rule validators, and schema conformance checks. The judicial pillar's critical property is independence — a worker cannot influence the judge that evaluates its output. A leader cannot disable the evaluation skills that run on its agents. The judicial process runs regardless of what the executive wants.
Judicial decisions are recorded by the media pillar. A failed evaluation, a low-confidence verdict, a panel disagreement — all of these are written to the audit log and surfaced to the review queue. The executive cannot suppress these records.

## [Heading3] Media pillar
The media pillar makes information visible to humans. This includes the audit log (append-only record of all agent actions and decisions), observability dashboards (real-time view of swarm behaviour), review queues (pending items awaiting human decision), and skill gap logs (patterns of capability shortfalls).
The critical property of the media pillar is that it is read-only from the executive's perspective. Agents write events; they cannot modify past events. Agents cannot delete entries. Agents cannot hide their actions from the media pillar. Even if an agent attempted to suppress a record, the attempt itself would be recorded. This is what makes agent behaviour ultimately accountable — the record persists regardless of agent intent.

## [Heading2] 8.4 Implementation in v1.0 — logical separation on AGT
CHANGED FROM v0.1 — In v0.4, this section described building the pillar implementations from scratch. In v0.5, the pillars are implemented on top of the Microsoft Agent Governance Toolkit (AGT) for the components AGT already provides well. Only the SwarmKit-specific aspects remain custom.
Implementing true process-level separation for all four pillars is substantial infrastructure work. v1.0 ships with the pillars logically separated — distinct modules, distinct identities, distinct storage boundaries — but running within a single runtime process. AGT provides battle-tested implementations of the underlying capabilities; SwarmKit wires them together and adds swarm-specific concepts on top.

## [Heading3] Pillar-to-AGT mapping
Each pillar is implemented using a combination of AGT packages and SwarmKit-native code:
Pillar
AGT components used
SwarmKit additions
What AGT doesn't cover
Legislative
Policy document loading, IAM scope definitions (via agent-os-kernel policy files)
Topology YAML schema, skill/archetype definitions, workspace config
Topology-as-data concept and workspace structure are SwarmKit-specific
Executive
Agent Mesh identity (Ed25519, DIDs) per agent; Agent Runtime sandboxing for generated code
Agent instantiation from archetypes, hierarchical agent tree, skill invocation
Hierarchical composition and the skill abstraction are SwarmKit-specific
Judicial
Agent OS policy engine (sub-millisecond p99), capability sandboxing, prompt injection detection
Decision-skill invocation orchestration, multi-persona panel composition, tiered escalation routing
Swarm-level evaluation workflow is SwarmKit-specific
Media
Agent SRE (OpenTelemetry, tracing, audit logging, SLO tracking), Agent Compliance (EU AI Act, HIPAA, SOC2 mapping)
Skill gap logs, review queue surfacing, topology-aware dashboards
Gap detection and swarm-specific observability are SwarmKit additions
The effect of this mapping: a substantial portion of governance infrastructure is already built, tested (AGT ships with 9,500+ tests), and regulation-ready. SwarmKit adds the swarm-level concepts that AGT does not cover. The combined system is more capable than either alone — AGT without topology-as-data is governance without composition; topology-as-data without AGT is composition without governance.

## [Heading3] Logical separation boundaries
Logical separation for v1.0 means:
Each pillar is implemented in a distinct module with a defined interface between pillars
AGT's policy engine runs as its own component with its own identity context
Audit events flow into AGT's telemetry and append-only storage; SwarmKit cannot modify past entries
Executive agents invoke actions through middleware that routes through AGT's ToolCallInterceptor — policy evaluation happens before execution, with no bypass path available to agents
The runtime enforces pillar boundaries at the module level — an executive call into the media module can only append, not modify
Full process-level separation — each pillar running as a separate service, communicating over defined protocols, with independent failure domains — is a v2.0 hardening target. AGT already supports sidecar deployment patterns on Azure AKS and equivalent infrastructure, which will be the path for production hardening.

## [Heading2] 8.5 The GovernanceProvider abstraction
CHANGED FROM v0.1 — Section 8.5 is new in v0.5. It introduces the abstraction layer that keeps SwarmKit portable across governance toolkit implementations.
Adopting AGT is the right choice today, but taking a direct runtime dependency creates vendor risk. If AGT is deprecated, if the project stagnates before reaching foundation governance, or if a better alternative emerges, SwarmKit should be able to switch without a framework-wide rewrite.
The abstraction is deliberately narrow — it covers the governance operations that any toolkit would expose, at a level of detail that is stable across implementations. The interface covers policy evaluation, identity verification, event recording, and trust scoring. Implementation-specific capabilities (AGT's specific compliance mappings, its specific cryptographic protocols, its specific telemetry formats) are not part of the abstraction.
# SwarmKit's internal interface (illustrative)class GovernanceProvider(ABC):        @abstractmethod    def evaluate_action(        self,        agent_id: str,        action: str,        context: dict    ) -> PolicyDecision:        """Ask the governance layer whether this action is allowed."""        ...        @abstractmethod    def verify_identity(        self,         agent_id: str,         credential: Credential    ) -> bool:        """Verify the agent's identity."""        ...        @abstractmethod    def record_event(        self,         event: AuditEvent    ) -> None:        """Append an event to the audit log (append-only)."""        ...        @abstractmethod    def get_trust_score(        self,         agent_id: str    ) -> float:        """Get the current trust score for the agent."""        ...# v1.0 ships with one concrete implementationclass AGTGovernanceProvider(GovernanceProvider):    """Implements GovernanceProvider on top of Microsoft AGT."""    # wraps agent-os-kernel, agentmesh-platform, agent-sre, etc.
This pattern is the same one Terraform uses for cloud providers — narrow interface, multiple implementations, genuine portability at a stable boundary. AGT becomes the v1.0 implementation; if a future replacement is needed, a new GovernanceProvider implementation is written. Everything above the abstraction (topology schema, runtime, skills, archetypes, UI) stays unchanged.
DESIGN NOTE — The execution engine (LangGraph) is deliberately NOT abstracted this way. See Section 13.2 for the investigation and rationale. In brief: governance interfaces are narrow and stable; execution engine interfaces are broad and evolving. The first is worth abstracting; the second is not. The topology schema is already the right abstraction for execution.

## [Heading2] 8.6 Token economics — the judicial tiering model
A naive implementation of the pillar model would evaluate every executive action through an LLM-powered judge, multiplying token costs substantially. This would make the framework impractical for cost-sensitive users. The judicial pillar therefore supports three tiers of evaluation with explicit routing rules — most of which are handled by AGT's deterministic policy engine at sub-millisecond latency, meaning near-zero token cost for the common path.
Tier
Implementation
When it fires
Tier 1 — Deterministic (AGT)
AGT's Agent OS policy engine. Sub-millisecond p99 latency. Schema validation, IAM scope checks, rate limits, capability checks, prompt injection detection.
Always. Every action flows through this tier. Zero LLM tokens.
Tier 2 — Single LLM judge
One LLM judge skill invocation with a structured rubric. Returns verdict + confidence.
When Tier 1 passes and the action requires semantic evaluation (quality, relevance, correctness). Moderate cost.
Tier 3 — Multi-persona panel
Multiple judge skills running in parallel with consensus logic. Higher cost, higher reliability.
When Tier 2 returns low confidence, or when the action crosses a sensitivity threshold (production writes, cross-zone handoffs, HITL-eligible outputs). Expensive.
The default policy: actions that pass Tier 1 proceed unless explicitly configured to require Tier 2 or 3. This means governance adds near-zero overhead to most actions. Expensive evaluation is reserved for the small fraction of actions where it genuinely matters.

## [Heading3] Governance overhead target
The framework sets an explicit, measurable target: governance should add 10-20% token overhead in typical use — not 300-400%. This target guides implementation decisions. If a feature's natural design would push governance cost significantly higher, the design is reconsidered rather than accepted. Users measuring the framework in production should be able to verify this target empirically.
Configurability exists where users explicitly accept higher overhead — a compliance-heavy deployment can escalate more actions to Tier 3. A solo developer's personal topology can disable all Tier 2 and 3 evaluation for non-production runs. The framework nudges toward sensible defaults without forcing them.

## [Heading2] 8.7 Capability scopes for evolution
The Separation of Powers model gives concrete meaning to the capability scopes introduced for swarm evolution. Several scopes are reserved for human identity only — no agent, regardless of role, is granted these by default:
Scope
What it controls
skills:write_pending
Ability to write proposed skill files to the pending-review directory. Granted to the Skill Authoring Swarm's Publication Worker.
skills:activate
Ability to move a skill from pending-review to active. Granted only to human identity.
mcp_servers:scaffold
Ability to generate MCP server scaffold code to the pending-review directory. Granted to the authoring swarm.
mcp_servers:deploy
Ability to start a generated MCP server as a live process. Granted only to human identity, with additional sandboxing requirements.
topologies:modify
Ability to modify topology files. Granted only to human identity.
iam:modify
Ability to modify IAM policy definitions. Granted only to human identity.
audit:modify
Ability to modify past audit entries. Does not exist. No identity, human or agent, has this scope.
The last row is the one that matters most. The framework is architected such that the audit log cannot be modified retroactively by anyone. Append is the only allowed operation. This is what makes the media pillar structurally independent — even the operators who administer the system cannot rewrite history.

## [Heading2] 8.8 MCP server authoring as a bounded capability
With the governance model established, MCP server authoring becomes tractable. The Skill Authoring Swarm handles it as a special case of skill authoring:
User describes a desired capability; the authoring swarm searches for suitable MCP servers
If a suitable server exists, the swarm authors a skill wrapping it (normal path)
If no suitable server exists, the swarm surfaces the limitation and offers two paths: scaffold-and-review (swarm generates server code; human reviews) or delegate-to-human (human provides server; swarm authors the wrapping skill)
On the scaffold path, the generated code lands in the pending-review directory (via `mcp_servers:scaffold` scope); activation requires human identity (`mcp_servers:deploy`)
Additional safeguards for generated servers: sandboxed execution in v1.0 (Docker or equivalent), automated static analysis as a v1.1 addition, recommended SAST tooling documented at launch
CHANGED FROM v0.1 — v0.3 left MCP server authoring as an open question. v0.4 resolves it: supported, through the same human-review pattern as skill authoring, with additional safeguards because generated code carries security implications that skill definitions do not.
The design acknowledges honestly that generated code review is harder than skill review. A generated MCP server could contain subtle backdoors or injection vulnerabilities that human review might miss. The framework's position: human review is necessary but not sufficient for generated code; automated tooling (static analysis, sandboxed execution, dependency scanning) is a recommended complement, and v1.1 will bundle initial tooling for this.

## [Heading1] 9. System Architecture

## [Heading2] 9.1 The three components
SwarmKit consists of three components that share a common topology and skill schema:
Component
Implementation
Distribution
swarmkit-runtime
Python package with CLI and HTTP server. Contains the topology interpreter, LangGraph integration, skill execution engine, IAM enforcement, audit logging, and built-in identity provider.
PyPI package, Docker image
swarmkit-ui
Next.js web application. Topology composer (Structure / Relationships / Network views), skill authoring interface, and runtime dashboard (review queues, run history, audit logs, skill gap logs, archetype and skill browser).
npm package, hosted demo, self-host instructions
swarmkit-schema
Language-agnostic JSON Schema with validators in Python and TypeScript. Covers topology, skill, workspace, and trigger schemas. Versioned independently.
PyPI, npm, JSON Schema URL

## [Heading2] 9.2 Component interactions
The three components share the topology and skill file formats but do not depend on each other at runtime:
The UI reads and writes topology files and skill files. It syncs to a workspace directory on disk, a git repository, or a backend service.
The runtime reads topology files and skill files. It does not need the UI to operate. Power users can author both in any text editor.
Both validate against the schema. The schema package provides validators.

## [Heading2] 9.3 Workspace structure
A workspace is a directory containing all the artifacts for a deployment:
my-workspace/├── workspace.yaml              # Workspace config, IAM, shared resources├── topologies/│   ├── code-review.yaml│   └── content-swarm.yaml├── skills/                     # NEW in v0.2│   ├── code-quality-review.yaml│   ├── github-pr-fetch.yaml│   └── slack-notify.yaml├── archetypes/│   ├── engineering-leader.yaml│   └── code-review-worker.yaml├── schedules/├── triggers/├── knowledge_bases/│   └── content-analytics/├── review_queues/│   └── content-recommendations/└── .swarmkit/    ├── audit/                  # Audit logs    ├── state/                  # State checkpoints    └── skill_gaps/             # NEW: Skill gap logs per leader

## [Heading1] 10. Topology Schema (High-Level)
This section describes the conceptual structure. Detailed specification is deferred to a separate schema document.

## [Heading2] 10.1 Top-level topology structure
apiVersion: swarmkit/v1kind: Topologymetadata:  name: code-review-swarm  version: 1.0.0  runtime:  mode: persistent              # one-shot | persistent | scheduled  max_concurrent_tasks: 5  task_timeout_seconds: 300  agents:  root: { ... }  leaders: [ ... ]  # Workers nested inside their leader  artifacts:  knowledge_bases: [ ... ]  review_queues: [ ... ]  audit:    level: detailed    storage: sqlite    retention_days: 90  skill_gap_logging:    enabled: true    surface_threshold: 5        # Surface gap after N occurrences

## [Heading2] 10.2 Agent definition
A simplified worker definition showing the skills-based model:
- id: code-review-worker  archetype: code-analyst-worker  role: worker  parent: engineering-leader    model:    provider: anthropic    name: claude-sonnet-4    temperature: 0.2      prompt:    system: |      You are a senior code reviewer specialising in Python and TypeScript.      skills:                       # UNIFIED in v0.2    - github-repo-read          # capability    - eslint-analyse            # capability    - code-quality-review       # decision (was: validation gate in v0.1)    - peer-handoff-qa           # coordination (was: a2a config in v0.1)    - llm-judge-self            # decision (was: evaluation in v0.1)    - audit-log-write           # persistence      iam:    base_scope: [repo:read]    elevated_scopes: []
CHANGED FROM v0.1 — In v0.1 this agent definition had separate fields for tools, validation, a2a_peers, evaluation, and communication. In v0.2 all of these collapse into a single skills array. The runtime resolves each skill by category and applies appropriate semantics.

## [Heading1] 11. The Reference Topology Library

## [Heading2] 11.1 v1.0 releases: three reference topologies
v1.0 ships with three reference topologies. Each works immediately, is production-quality, and serves as a learning artifact for the framework's core patterns.

## [Heading3] Code Review Swarm
Demonstrates multi-agent coordination. Three-leader hierarchy (Engineering, QA, Operations) with workers under each. A2A handoffs between leaders. Validation skills at handoff points (any compatible MCP validator including Rynko Flow). LLM judge skills at each leader before handoff. Mandatory HITL on production deployments. Guarded cross-leader channel example. GitHub webhook trigger configuration.

## [Heading3] Skill Authoring Swarm
Demonstrates skill-level extensibility. Conversation-led skill authoring. User describes a desired capability in natural language; the authoring swarm asks clarifying questions, drafts the skill definition, generates input/output schemas, determines required IAM scopes, selects an implementation strategy (MCP tool, LLM prompt, composed skill), runs mandatory validation tests against real MCP servers, and publishes only on successful test.

## [Heading3] Workspace Authoring Swarm
Demonstrates workspace-level bootstrap. A new user runs swarmkit init in an empty directory; the authoring swarm conducts a conversation about their use case, data sources, compliance needs, and team structure. It then produces a complete workspace scaffold — topology files referencing appropriate archetypes, starter skills for the identified use case, workspace.yaml with sensible defaults, schedules and triggers if applicable. Publishes only after user confirms the scaffold matches intent.
The three topologies together tell the launch story: structured coordination (Code Review), skill-level extensibility (Skill Authoring), and workspace-level bootstrap (Workspace Authoring). A new user experiences the framework's value within minutes — not by reading documentation but by running swarmkit init and answering questions.

## [Heading2] 11.2 v1.x roadmap
Release
Topology
Pattern demonstrated
Target
v1.0
Code Review + Skill Authoring + Workspace Authoring
Coordination + skill extensibility + workspace bootstrap
Launch
v1.1
Content Generation Swarm
Multi-modal, multi-persona eval, feedback loops
Launch + 4 weeks
v1.2
Document Processing Swarm
High-volume parallel processing, validation skills
Launch + 8 weeks
v1.3
Customer Support Swarm
Reactive routing, RAG, confidence escalation
Launch + 12 weeks
v1.4
Codebase Knowledge Swarm
Self-built knowledge base, advisory HITL
Launch + 16 weeks
v2.0
Home Assistant Swarm
Always-on multi-swarm coordination
Launch + 6 months

## [Heading1] 12. Skill Authoring & Swarm Evolution
CHANGED FROM v0.1 — This section is new in v0.2. It describes how swarms grow their capabilities over time, the Skill Authoring Swarm's role, and the governance model for swarm evolution.

## [Heading2] 12.1 The gap-detection loop
Every leader in a SwarmKit deployment maintains a Skill Gap Log. Entries are written automatically by the runtime when specific conditions occur:
A worker produces a low-confidence output that routes to HITL more than a threshold number of times for similar inputs
A leader receives a task it cannot delegate because no worker has the required skills
A validation skill fails consistently against a particular input pattern
A human explicitly marks a HITL review as "should have been automated"
Each entry captures the triggering input, the context, what was expected, and what actually happened. Over time, the gap log accumulates structured evidence about where the swarm's current skill set falls short.

## [Heading2] 12.2 Gap surfacing
The runtime periodically reviews the Skill Gap Log and surfaces patterns that appear frequently enough to warrant authoring. Surfacing produces a recommendation with:
A description of the observed gap in plain language
Representative examples from the gap log
A proposed skill category and outline
An estimate of how many past incidents the new skill would have handled
This recommendation appears in the runtime dashboard's review queue. Humans decide whether to proceed with authoring, defer, or dismiss. Nothing happens autonomously.

## [Heading2] 12.3 The Skill Authoring Swarm
When a human approves gap-authoring, the Skill Authoring Swarm engages. Its structure:

## [Heading3] Conversation Leader
Front-of-house agent that interacts with the user. Asks clarifying questions, confirms understanding, walks through structured previews. The interaction model is deliberately conversational — users describe intent in natural language, the swarm extracts structured information through dialogue.

## [Heading3] Specification Workers
Drafting specialists: input schema drafter, output schema drafter, implementation selector, IAM scope analyser, description writer, test-case generator. Each produces a component of the skill definition. The Conversation Leader routes to workers based on what the skill needs.

## [Heading3] Review Leader
Validates the drafted skill against the schema, checks for common anti-patterns (overly broad IAM, vague descriptions, missing error handling), and surfaces issues back to the Conversation Leader for resolution.

## [Heading3] Test Execution Leader
Runs the drafted skill against sample inputs. If the skill calls an MCP tool, the test actually invokes the MCP server and captures the response. If the skill runs an LLM prompt, the test invokes the model. Test results are shown to the user before publication.

## [Heading3] Publication Worker
On test success and user approval, writes the skill file to the workspace and optionally publishes to a shared registry.

## [Heading2] 12.4 Authoring flow
The flow the user experiences:
Phase 1: Describe. User describes the desired skill in natural language, either by invoking authoring directly or by approving a gap-surfaced recommendation.
Phase 2: Clarify. The Conversation Leader asks clarifying questions. What data does the skill read? What does it produce? What should happen on failure? Each answer fills in a field that the drafting workers use.
Phase 3: Preview. The swarm presents a structured preview of the drafted skill — metadata, schemas, implementation, IAM scopes. User can edit directly or ask the swarm to change something through further conversation.
Phase 4: Test. The swarm runs the skill against sample inputs. Real MCP calls, real responses, real timing. User confirms the output matches intent.
Phase 5: Publish. On user approval, the skill is written to the workspace. Provenance is marked as authored_by_swarm. The skill becomes available to agents immediately.

## [Heading2] 12.5 Governance
Swarm-authored skills require additional oversight. By default:
Newly authored skills cannot be invoked on production data until a human reviews and approves the first real execution
Authored skills are version-controlled and can be rolled back at any point
The audit log captures the authoring conversation, the draft iterations, the test results, and the publication decision
Skills authored by a swarm can be promoted to human provenance after human review and edit — signalling that a human has taken ownership
DESIGN PRINCIPLE — Swarm evolution is evidence-driven and human-gated. The framework observes and surfaces; humans decide. There is no autonomous self-modification.

## [Heading1] 13. Archetype Library
Archetypes are pre-configured agent definitions that can be referenced from any topology. An archetype bundles a role (root, leader, or worker), a default model configuration, a prompt template, and a skill set. Topologies reference archetypes by ID and override fields as needed.
For the distinction between archetypes and skills, see Section 6.6. In brief: archetypes are nouns (kinds of agents), skills are verbs (things agents do). Archetypes reference skills; topologies reference archetypes. Both are independently versioned and shareable.
CHANGED FROM v0.1 — v0.3 adds archetype provenance, matching the provenance property introduced for skills in v0.2. Archetypes authored by a swarm follow the same human-review-before-production pattern.

## [Heading2] 13.1 v1.0 archetype catalogue
v1.0 ships with approximately fifteen archetypes covering the patterns needed by Code Review Swarm and Skill Authoring Swarm, plus a general-purpose set:
Category
Archetype
Purpose
Coordination
supervisor-leader
Default leader archetype, basic delegation
Coordination
judge-and-handoff-leader
Validates output before passing on
Coordination
conversation-leader
Front-of-house conversational interaction
Coordination
escalation-leader
Handles cases workers can't resolve
Analysis
code-analyst-worker
Parses and evaluates code structure
Analysis
schema-drafter-worker
Produces JSON Schema from descriptions
Generation
code-gen-worker
Generates code modifications
Generation
prompt-writer-worker
Crafts LLM prompts for other agents
Evaluation
llm-judge-worker
Single-persona quality evaluation
Evaluation
security-reviewer-worker
Specialised security evaluation
Evaluation
schema-validator-worker
Deterministic schema conformance check
Action
mcp-caller-worker
Generic MCP tool invocation
Action
notification-sender-worker
Slack, Teams, email
Retrieval
file-context-worker
Repository and filesystem retrieval
Retrieval
vector-search-worker
Semantic search over knowledge bases

## [Heading2] 13.2 Archetype contribution
Archetypes are version-controlled and installable through the same registry mechanism as skills. The CLI supports installation:
swarmkit archetype install community/data-analyst-worker@2.1.0swarmkit archetype listswarmkit archetype publish ./my-archetype/

## [Heading1] 14. Runtime Architecture

## [Heading2] 14.1 Three execution modes
Mode
Use case
Invocation
One-shot
CLI execution, batch processing, single-task workflows
swarmkit run topology.yaml --input '...'
Persistent
Long-running swarms accepting tasks via HTTP
swarmkit serve topology.yaml --port 8000
Scheduled / event-triggered
Cron schedules, webhooks, file watches
swarmkit serve workspace/

## [Heading2] 14.2 Authoring entry points
CHANGED FROM v0.1 — Section 14.2 is new in v0.6. It documents the conversational authoring entry points that make v1.0 usable for non-developers without a visual UI.
The framework's conversational authoring pattern is exposed through CLI entry points that launch the relevant authoring swarm in terminal chat mode. Each entry point is a thin wrapper — it locates the correct reference topology, passes through any context (current workspace, target artifact name), and launches the swarm through the standard runtime.
Command
Launches
swarmkit init
Workspace Authoring Swarm. Run in an empty directory; produces a complete workspace scaffold through conversation.
swarmkit author topology [name]
Topology Authoring Swarm variant. Authors a new topology in an existing workspace.
swarmkit author skill [name]
Skill Authoring Swarm. Authors a new skill.
swarmkit author archetype [name]
Archetype Authoring Swarm variant. Authors a new archetype.
The authoring swarms are first-class topologies that ship as reference artifacts (Section 11). This means the same topologies that demonstrate the framework's capability power its own on-ramp. A user who runs swarmkit init is already using SwarmKit — not a separate bootstrap tool. The experience validates the framework immediately.
Advanced users who prefer direct YAML editing are unaffected. Hand-written topology files, skill files, and archetype files work identically to swarm-authored ones. The authoring path is an additional option, not a required one. Both paths produce the same artifacts and go through the same schema validation.

## [Heading2] 14.3 Topology interpretation
On startup, the runtime loads and validates the topology and all referenced skills, then dynamically constructs a LangGraph StateGraph. The construction process:
Parse and validate topology against schema
Resolve all archetype references and merge configuration
Resolve all skill references; validate each skill's schema and implementation availability
Construct LangGraph nodes for each agent
Construct LangGraph edges from hierarchy and communication-skill definitions
Wire in decision-skill invocations as evaluation nodes
Wire in persistence-skill invocations for audit, knowledge base writes, and skill gap logging
Configure checkpointing, retries, and HITL interrupts
Compile the graph

## [Heading2] 14.4 The eject command
Users can export the LangGraph code that the runtime would execute:
swarmkit eject topology.yaml --output ./generated/# Produces:#   ./generated/swarm.py        - LangGraph graph definition#   ./generated/agents.py       - Agent function implementations#   ./generated/skills.py       - Skill implementations#   ./generated/requirements.txt#   ./generated/README.md
After ejection, the user owns the generated code. SwarmKit becomes optional. This is both a user freedom and a forcing function for the abstraction.

## [Heading2] 14.5 State, checkpointing, and the skill gap log
LangGraph's checkpointing primitives are exposed through the topology schema. Checkpoints persist to SQLite in the workspace by default, with Postgres supported for production. Skill gap logs use the same storage configuration as audit logs.

## [Heading1] 15. User Interface

## [Heading2] 15.1 Three primary surfaces
The UI provides three surfaces:
Surface
Purpose
Topology Composer
Design and edit topology files. Hybrid form-driven and visual interface. Outputs valid YAML/JSON.
Skill Authoring Interface
Conversational interface to the Skill Authoring Swarm. Chat-driven skill creation with structured previews and test execution.
Runtime Dashboard
Operate running swarms. Review queues, run history, audit log viewer, skill gap logs, archetype and skill browser.
CHANGED FROM v0.1 — The Skill Authoring Interface is new in v0.2. It is the UI front-end to the Skill Authoring Swarm — conversational interaction rather than form-filling.

## [Heading2] 15.2 The three composer views
View
Optimised for
Visual style
Structure View
Defining agent hierarchy, levels, membership
Org-chart layout. Root at top, leaders in columns, workers under each. Click to open inspector.
Relationships View
Configuring per-agent skills, IAM, communication
Zoomed view of a single agent showing direct peers, skills, validation gates, IAM scopes.
Network View
Understanding the swarm as a communication graph
Flat node-and-edge layout. Useful for structural review.

## [Heading2] 15.3 Runtime dashboard
Operational visibility once a swarm is running:
Active runs and current state
Pending items in review queues with one-click approve/reject/edit
Audit log search and filtering
Skill gap log viewer, with one-click "start authoring" for surfaced gaps
Knowledge base inspection
Schedule and trigger management
Archetype and skill catalogue browsing and installation
OPEN QUESTION FOR REVIEW — Should the v1.0 UI be released as part of v1.0 or deferred to v1.1? Runtime + CLI alone is useful and power users can author topologies and skills in their editor. Deferring UI reduces v1.0 scope by 6-8 weeks but slows non-developer adoption. Recommendation: v1.0 ships runtime + CLI with the Skill Authoring Swarm accessible via terminal chat mode. UI (composer + authoring interface + dashboard) ships as v1.1.

## [Heading1] 16. Identity, Access & Governance
CHANGED FROM v0.1 — v0.5 updates this section to reflect AGT's role. Agent identity, policy enforcement, and audit logging are provided by AGT's Agent Mesh, Agent OS, and Agent SRE packages respectively. The SwarmKit-specific layer is the mapping from topology concepts (agents, skills, workspaces) to AGT's primitives (DIDs, policy rules, events).

## [Heading2] 16.1 Two-layer identity model
SwarmKit distinguishes human identity from agent identity, and uses different mechanisms for each.
Human identity is handled by SwarmKit's pluggable identity layer. v1.0 ships with a built-in provider for solo developers and small teams, supporting user accounts, team membership, and role-based access at workspace and topology levels. For organisations with existing identity infrastructure, v1.x adds adapters for Auth0, Okta, Google Workspace, Azure AD, and generic OIDC. This layer is SwarmKit-specific because it integrates with topology-level concepts that AGT does not know about.
Agent identity is handled by AGT's Agent Mesh package. Every agent instantiated from a topology receives a decentralised identifier (DID) and an Ed25519 keypair. Inter-agent communication uses Agent Mesh's Inter-Agent Trust Protocol with mutual authentication and short-lived capabilities. The trust scoring system (0-1000 scale across five behavioural tiers) tracks agent behaviour over time, enabling trust decay when an agent exhibits anomalies. This is production-grade identity infrastructure that would have taken substantial effort to build from scratch.

## [Heading2] 16.2 Access control models
Model
Layer
Implementation
RBAC
Provisioning
SwarmKit's identity layer. Static role-to-permission mapping at user and team levels.
PBAC
Runtime
AGT's Agent OS policy engine. Evaluates every skill invocation at sub-millisecond p99. Supports YAML rules, OPA Rego, and Cedar policy languages.
Zero Trust + Least Privilege
Cross-cutting
AGT's Agent Mesh. Every agent has its own DID. Read-only by default. Write requires audited elevation.

## [Heading2] 16.3 Per-agent IAM scopes and per-skill scope requirements
Every agent declares its base IAM scope in the topology file. Every skill declares the scopes it requires to execute. At runtime, AGT's Agent OS policy engine enforces that an agent can only invoke a skill if the agent's scopes include all the scopes the skill requires. Elevation happens through audited approval steps when a skill needs more scope than the agent's base.
SwarmKit's contribution here is the mapping — translating topology-level agent definitions into AGT capability models, and topology-level skill definitions into AGT action specifications. AGT does the enforcement; SwarmKit defines what's being enforced.

## [Heading2] 16.4 Audit logging
Audit logging is handled by AGT's Agent SRE package. Every skill invocation, tool call within a skill, state transition, and human decision flows into AGT's telemetry pipeline. Agent SRE integrates with existing observability stacks through adapters for Datadog, PagerDuty, Prometheus, OpenTelemetry, Langfuse, LangSmith, Arize, and MLflow. Append-only semantics are enforced at the storage layer — no identity, human or agent, has scope to modify past entries.
SwarmKit adds swarm-specific event types on top of AGT's base telemetry: skill gap events, review queue state changes, gap surfacing decisions, authoring swarm conversations. These are emitted through AGT's standard event interface, giving them the same append-only guarantees and observability-stack compatibility as any other audit event.

## [Heading2] 16.5 The GovernanceProvider abstraction in practice
Everything described in this section goes through the GovernanceProvider interface (Section 8.5). The AGTGovernanceProvider implementation wraps agent-os-kernel for policy evaluation, agentmesh-platform for identity, and agent-sre for telemetry. If a future replacement is needed, a new GovernanceProvider implementation is written — the topology schema, the runtime, the skills, and the user experience stay unchanged.

## [Heading1] 17. Quality Evaluation

## [Heading2] 17.1 Evaluation as a skill category
Quality evaluation is a skill category (Decision). Single-persona judges, multi-persona panels, business rule validators, and schema conformance checks are all decision skills. The framework treats them uniformly; the differences are in implementation.

## [Heading2] 17.2 The three evaluation tiers
Evaluation follows the tiered model defined in Section 8.6. The critical cost-optimisation insight is that Tier 1 (deterministic policy enforcement through AGT's Agent OS) is nearly free and handles the vast majority of cases. Tier 2 and Tier 3 invoke LLMs and are reserved for cases where semantic evaluation is genuinely required.
Tier
Implementation
When to use
Tier 1 — Deterministic (AGT)
AGT's Agent OS policy engine. Schema conformance, IAM checks, rate limits, capability boundaries, prompt injection detection.
Always. Runs before any LLM evaluation. Sub-millisecond, zero token cost.
Tier 2 — Single LLM judge
One LLM judge skill invocation against a rubric. Returns verdict with confidence score.
When Tier 1 passes and semantic evaluation is needed. Default for most content-quality checks.
Tier 3 — Multi-persona panel
Composite skill invoking multiple single-judge skills in parallel with consensus logic. Escalates to HITL on panel disagreement.
When Tier 2 returns low confidence, or when the action crosses a sensitivity threshold. Expensive; used sparingly.

## [Heading2] 17.3 Validation skills from any provider
Any MCP server that exposes validation tools can provide validation skills to SwarmKit. This includes open-source validators, homegrown business rules engines, commercial services, and Rynko Flow. The framework is vendor-neutral at the architectural level. Users choose validation providers based on capability and requirements, not framework lock-in.
AGT's policy engine and MCP-based validation skills are complementary, not competing. AGT enforces policy at the action level (may this agent invoke this tool?). Validation skills operate at the semantic level (is this output correct?). A typical flow: AGT policy check passes, skill invokes MCP validation tool, tool returns verdict, decision skill records result for judicial pillar.
CHANGED FROM v0.1 — v0.1 had a dedicated section on Rynko Flow integration. v0.2 removed it. v0.5 retains the framework-agnostic position — Rynko Flow is one of many possible validation skill providers, accessed through standard MCP. No framework-level coupling.

## [Heading1] 18. MCP Integration & External Tools

## [Heading2] 18.1 MCP as the universal external interface
SwarmKit uses MCP (Model Context Protocol) as the standard mechanism for skills to access external capabilities. Every capability skill that invokes an external service is implemented as an MCP tool call.
This produces several benefits:
The growing MCP server ecosystem becomes immediately available to SwarmKit skills
Skill IAM scopes map cleanly to MCP server permissions
Custom integrations are built by writing an MCP server, which is a well-documented pattern
External validation providers integrate through the same mechanism as any other MCP tool
AGT's MCP security gateway wraps MCP tool invocations with policy enforcement — every MCP call is evaluated by Agent OS before execution

## [Heading2] 18.2 A2A for inter-agent communication
Coordination skills that hand off between leaders use Google's A2A protocol where supported. For agents that do not natively support A2A, the runtime provides an adapter layer that wraps them in A2A-compatible Task semantics. A2A messages between agents are authenticated by AGT's Agent Mesh — each agent's DID signs its outbound messages, and receiving agents verify signatures before acting.

## [Heading2] 18.3 Example — validation through MCP
A skill that validates an invoice through Rynko Flow:
id: invoice-validation-gatecategory: decisiondescription: Validates invoice structure and content via Rynko Flow gate  inputs:  invoice: { type: object, required: true }  outputs:  verdict: { type: enum, values: [pass, fail] }  run_id: { type: string, description: Attestation Run ID on pass }  implementation:  type: mcp_tool  server: rynko_flow       # Any MCP server; Rynko is one option  tool: validate_invoice_v3  provenance:  authored_by: human  version: 1.0.0
An equivalent skill using a different validator — say, an open-source JSON Schema validator — would have the same schema and a different `server` value. Topologies can mix validation skills from different providers freely.

## [Heading1] 19. Comparison to Existing Tools
Tool
What it is
Overlap with SwarmKit
Difference
LangGraph
Stateful graph framework for multi-agent workflows
Both support multi-agent coordination
LangGraph is code-first; SwarmKit is data-first. SwarmKit uses LangGraph as runtime.
CrewAI
Role-based multi-agent framework
Agent hierarchies and crews
CrewAI is code-first with limited dynamic topology; SwarmKit is dynamic and extensible through skill authoring.
n8n
Visual workflow automation with AI nodes
Visual composition
n8n is workflow-oriented (linear graphs); SwarmKit is swarm-oriented (hierarchical agents with growth).
OpenClaw
Personal AI assistant runtime
Configuration-driven, local-first, open source, skills-based
OpenClaw is single-agent personal scope; SwarmKit is multi-agent team scope with swarm-level coordination and evolution.
Salesforce Agentforce
Enterprise agent platform within Salesforce
Multi-agent enterprise use cases
Agentforce is closed and Salesforce-bound; SwarmKit is open and standalone.

## [Heading1] 20. Release Plan

## [Heading2] 20.1 Phased delivery
Phase
Scope
Validates
Effort
Phase 1: Core (v1.0)
Schema, runtime, CLI, Code Review Swarm, Skill Authoring Swarm, Workspace Authoring Swarm, authoring CLI entry points, ~15 archetypes, ~20 starter skills, GovernanceProvider abstraction with AGT implementation, pillar wiring, tiered judicial evaluation, topology-to-AGT mapping, skill gap logging
Topology-as-data approach; skill abstraction; conversational authoring; governance via AGT; GovernanceProvider portability; non-developer on-ramp via conversational CLI
13-16 weeks
Phase 2: UI (v1.1)
Topology composer, skill authoring interface, runtime dashboard, archetype and skill browser
Visual composition value; non-developer adoption path
8-10 weeks
Phase 3: Topology library (v1.2-1.4)
Content, Document Processing, Customer Support, Codebase Knowledge swarms
Framework generalisation
2-3 weeks per swarm
Phase 4: Showcase (v2.0)
Home Assistant Swarm, cross-swarm rules, advanced trigger filtering, registry service, process-level pillar separation
Long-running multi-swarm coordination; production hardening
10-12 weeks
CHANGED FROM v0.1 — Phase 1 effort adjusted from 11-13 weeks in v0.5 to 13-16 weeks in v0.6, adding 2-3 weeks for the Workspace Authoring Swarm and the authoring CLI entry points. The trade is worth making — v1.0 becomes meaningfully more accessible to non-developers, and v1.1 UI work shrinks correspondingly (it becomes a presentation layer on existing authoring swarms rather than new infrastructure). Net effort across v1.0 and v1.1 is approximately unchanged; the distribution across releases is better.

## [Heading2] 20.2 Distribution and community
PyPI for the runtime package
GitHub repository as primary documentation and source
npm for schema validators
Docker Hub for runtime images
Documentation site with launch
Skill registry (v1.2+) as GitHub organisation or dedicated service
Community channels follow standard developer tool patterns: Discord for real-time discussion, GitHub Discussions for asynchronous Q&A, weekly digest blog posts about user contributions.

## [Heading1] 21. Open Questions for Review
Decisions needed before detailed design begins. Questions resolved by AGT adoption in v0.5 are no longer listed; new AGT-related questions have been added.
Question
Recommendation
Decision needed
Schema canonical format: YAML, JSON, or both?
JSON canonical, YAML primary user-facing, both fully supported
Confirm
Should v1.0 ship with the UI, or defer UI to v1.1?
Defer UI to v1.1. Ship three authoring swarms accessible via CLI chat mode in v1.0 (swarmkit init, swarmkit author ...). This provides non-developer on-ramp without requiring visual UI infrastructure. v1.1 UI becomes a nicer front-end to the same authoring swarms.
Confirm
License: MIT or Apache 2.0?
MIT for maximum permissiveness, matching AGT's licence
Confirm
Project naming: SwarmKit, or something else?
Verify domain and GitHub org availability before lock-in
Decide
Backend language: Python only, or also TypeScript?
Python only for v1.0. AGT's Python SDK is the most complete; other SDKs are available if expansion is needed later.
Confirm
Hosting model: pure self-host, or also managed cloud?
Self-host only for v1.0; managed cloud as future commercial consideration
Confirm
Skill registry: v1.0, v1.1, or later?
Workspace-local and git-URL installation in v1.0. Proper registry service in v1.2 once community activity warrants it.
Confirm
Should newly authored skills require human approval on first production execution?
Yes, always for authored_by_swarm provenance. Configurable threshold for other provenances.
Confirm
Sandboxing requirement for generated MCP servers?
Mandatory Docker or equivalent process isolation in v1.0, enforced through AGT's Agent Runtime execution rings. Static analysis tooling bundled in v1.1.
Confirm
Governance overhead target for typical workloads?
10-20% token overhead. AGT's sub-millisecond Tier 1 enforcement makes this target easily achievable; Tier 2 and Tier 3 are opt-in per action.
Confirm
AGT version pinning strategy?
Pin to a specific minor version in Phase 1. Track upstream closely through v1.0 period. Evaluate each AGT release for adoption rather than auto-updating.
Confirm
Policy language for SwarmKit's AGT configuration — YAML rules, OPA Rego, or Cedar?
YAML rules for v1.0 (simplest for users). Rego and Cedar available for advanced users who need expressiveness. AGT supports all three natively.
Confirm
What happens if AGT is not available on the user's target deployment platform?
AGT's Python SDK is the v1.0 requirement. If an organisation requires a non-Python deployment, the GovernanceProvider abstraction allows a future implementation against AGT's TypeScript, .NET, Go, or Rust SDKs.
Acknowledge

Once these questions are resolved, the next artifacts to produce are:
Detailed schema specification (topology, skill, workspace, trigger, IAM policy schemas)
Runtime API specification (CLI commands, HTTP endpoints, Python SDK)
GovernanceProvider interface specification and AGTGovernanceProvider implementation details
Skill Authoring Swarm detailed design (agent prompts, conversation flow, test execution)
Workspace Authoring Swarm detailed design (conversation flow for bootstrap, use-case detection, scaffold generation)
UI specification (component wireframes, interaction patterns) — for v1.1
Code Review Swarm detailed design (actual YAML for v1.0)
Archetype catalogue (fifteen v1.0 archetypes with full definitions)
Starter skill catalogue (twenty v1.0 skills with full definitions)
AGT policy library for v1.0 (default policies, deployment patterns, customisation guide)
Each detailed design references this v0.6 document as the source of conceptual decisions. Implementation begins after all detailed designs are complete and reviewed.
End of design document v0.6