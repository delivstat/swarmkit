# Building swarms — the complete playbook

This is the end-to-end guide to building a **complete automated agent swarm** with SwarmKit — from a single agent to a governed, multi-app delivery pipeline that a controller sequences as a weeks-long saga. It is written to be read top to bottom: each step adds exactly one capability, shows the smallest real artifact that unlocks it, gives the command to run it, and links to the deep reference.

If you only read one thing first, read the mental model. Everything else is a specialisation of it.

!!! tip "For LLMs and coding agents"
    The repo root ships [`llms.txt`](https://github.com/delivstat/swarmkit/blob/main/llms.txt) — a compact, link-rich map of every feature with inline schemas. Load it into context first; use this playbook for the ordered build recipe and the worked example.

## The mental model

Three claims, in priority order — they are the tie-breakers for every design decision:

1. **Topology is data.** A swarm is YAML/JSON the runtime *interprets*. There is no code-generation step and no "compile to Python" escape hatch — the portability guarantee is the openness of the artifacts plus the open-source runtime. A different swarm is new *data*, never new framework code.
2. **Skills are the only capability-extension primitive.** When you want an agent to be able to *do* something new, you add a skill (or compose existing ones). You never bolt on a parallel capability mechanism. *How* a node executes — a model call vs. a coding harness — is a separate **executor** seam, not a capability.
3. **Swarms grow through human-approved authoring.** The runtime records the capability gaps it hits; you surface them, author a skill through conversation, test it, and publish — gated at every step. A swarm you run is a swarm that tells you how to improve it.

Everything below is built out of a small vocabulary of artifact kinds. Learn these ten nouns and the two embedded configs and you can read any SwarmKit workspace.

## The artifact kinds

Every artifact is a YAML/JSON file starting with `apiVersion: swarmkit/v1` and a `kind`. There are **eleven canonical schemas** — ten standalone artifact kinds plus one embedded config (`ApprovalPolicy`, which lives inside a gate, not on its own).

| Kind | What it is | Reference |
|---|---|---|
| `Workspace` | The root manifest — names the workspace, picks the governance provider, wires servers/memory/canary. | [workspace](../reference/workspace.md) |
| `Topology` | A bounded swarm run — the agents, their roles, delegation edges, IAM scopes. The unit the runtime executes. | [topology](../reference/topology.md) |
| `Archetype` | A reusable agent template — model/prompt/skills/IAM/executor defaults a topology node instantiates. | [archetypes](../reference/archetypes.md) |
| `Skill` | A capability, decision, coordination, or persistence unit — the only capability-extension primitive. | [skills](../reference/skills.md) |
| `Funnel` | A reusable per-artifact quality gate: `validate → judge → review → approve`, referenced by id from a node or stage. | [funnel](../reference/funnel.md) |
| `StageGraph` | A whole pipeline as data — ordered bounded stages a controller sequences as a saga. | [stage-graph](../reference/stage-graph.md) |
| `Contract` | An integration contract between apps — makes a StageGraph stage's `locks` a checked, pickable vocabulary. | [contract](../reference/contract.md) |
| `RoleRegistry` | Named roles → member identities + the scopes they confer — how approval rules resolve to real people. | [role-registry](../reference/role-registry.md) |
| `Trigger` | An external event source (webhook/schedule) that starts a topology or emits a pipeline event. | [trigger](../reference/trigger.md) |
| `ExecutorAdapter` | A declarative adapter (`adapter.yaml`) that runs a coding harness as a node — data, not per-harness Python. | [executor-adapter](../reference/executor-adapter.md) |
| `ApprovalPolicy` | **Embedded config** (no `kind`) inside a gate's `approve:` — the multi-party rules, quorum, four-eyes floor. | [approval-policy](../reference/approval-policy.md) |

## Step 0 — install and scaffold

```bash
pip install swarmkit-runtime          # the runtime + CLI
swarmkit init                          # scaffold a workspace through conversation
```

`swarmkit init` is a conversational authoring swarm — you describe what you want and it produces the workspace, topology, archetypes, and skills as artifacts you own and can edit. You can equally hand-write the files; the rest of this guide shows the artifacts directly so you can read any workspace, however it was authored.

A workspace root is one file:

```yaml
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: my-swarm
  name: My Swarm
governance:
  provider: mock          # `mock` for local dev; `agt` (Microsoft AGT) for real policy/audit
```

→ [Workspace reference](../reference/workspace.md) · [Installation](../getting-started/install.md)

## Step 1 — one agent

A **Topology** is the unit the runtime runs. The smallest one is a single root agent instantiating an **Archetype**:

```yaml
# archetypes/business-analyst.yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata: { id: business-analyst, name: Business Analyst }
role: leader
defaults:
  model: { provider: openrouter, name: openai/gpt-4o-mini, temperature: 0.3 }
  prompt:
    system: >
      You are a business analyst. Read the requirement, identify the business flows
      it touches, and produce a clear, testable summary plus affected applications.
  iam:
    base_scope: [kb:read, kb:write]
provenance: { authored_by: human, version: 1.0.0 }
```

```yaml
# topologies/intake.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata: { name: intake, version: 0.1.0 }
agents:
  root:
    id: intake
    role: root
    archetype: business-analyst
    iam:
      base_scope: [kb:read, app:oms:read]     # this run's authority — least privilege
```

```bash
swarmkit validate .                    # resolve + type-check the whole workspace
swarmkit run intake                    # one-shot execution
```

The archetype carries the *reusable* defaults; the topology node carries the *run-specific* wiring (id, IAM scopes). IAM scopes are structural — an agent can only touch what its `base_scope` grants.

→ [Topology reference](../reference/topology.md) · [Archetypes](../reference/archetypes.md) · [Tutorial 1: Hello World](../tutorials/01-hello-world.md)

## Step 2 — give it a skill

A **Skill** is how an agent gains a capability. Skills come in four categories — `capability` (do a thing), `decision` (judge/score), `coordination` (route work), `persistence` (remember). A decision skill produces a structured verdict:

```yaml
# skills/impact-analysis.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata: { id: impact-analysis, name: Impact Analysis }
category: decision
outputs:                                 # structured output — validated before anyone reads it
  type: object
  properties:
    affected_apps: { type: array, items: { type: string } }
    reasoning: { type: string }
  required: [affected_apps, reasoning]
implementation:
  type: llm_prompt
  prompt: >
    Given the requirement and the apps' architecture summaries, decide which apps are
    affected and why. Return affected_apps (ids), rationale, and any open_questions.
provenance: { authored_by: human, version: 1.0.0 }
```

Attach it in the archetype: `skills: [impact-analysis]`. The `outputs` schema is enforced by the runtime — the model's answer is validated and field-corrected before it flows anywhere, so shape-level hallucination never propagates.

→ [Skills reference](../reference/skills.md) · [Structured output governance](../design-notes/structured-output-governance.md) · [Tutorial 3: Skills](../tutorials/03-skills.md)

## Step 3 — many agents

Add nodes and delegation. SwarmKit's compiler runs independent work in parallel and dependent work in order via `depends_on`, and coordinators use **structured delegation** — a planner builds a dependency-ordered task plan (`create-task-plan`) instead of ad-hoc prose hand-offs. This is the difference between a swarm that reliably fans out and one that loses track of its own work.

```yaml
agents:
  root:
    id: architect
    role: root
    archetype: solution-architect
    children: [oms-dev, web-dev]        # delegates to focused workers
  oms-dev:  { id: oms-dev,  role: worker, archetype: developer }
  web-dev:  { id: web-dev,  role: worker, archetype: developer }
```

→ [DAG dependency graph](../design-notes/dag-dependency-graph.md) · [Tutorial 4: Multi-Agent](../tutorials/04-multi-agent.md) · [Tutorial 6: Structured Delegation](../tutorials/06-structured-delegation.md)

## Step 4 — real tools via MCP

Agents get tools by connecting to **MCP servers** (stdio or Streamable HTTP), configured in the workspace — never coded per-vendor. Every tool call routes through governance, so an agent can only invoke tools its scopes allow. SwarmKit ships its own servers too (`swarmkit knowledge-server`, `swarmkit docs-reader`).

→ [MCP client](../design-notes/mcp-client.md) · [MCP discovery pattern](../design-notes/mcp-discovery-pattern.md) · [Tutorial 5: MCP Tools](../tutorials/05-mcp-tools.md)

## Step 5 — governance and decision skills

Governance is not a prompt suggestion — it is structural. All policy/identity/audit flow through the `GovernanceProvider` interface; the audit log is append-only from the executive's perspective; and a set of scopes reserved for human identity (`skills:activate`, `topologies:modify`, `iam:modify`, and the pipeline scopes below) can **never** be granted to an agent, regardless of prompt. **Decision skills** run mandatory evaluations at workspace/topology boundaries with a bounded retry loop.

→ [Governance provider](../design-notes/governance-provider-interface.md) · [Structured output governance](../design-notes/structured-output-governance.md) · [Tutorial 7: Governance & Safety](../tutorials/07-governance.md)

## Step 6 — a quality gate with a Funnel

A **Funnel** chains four optional layers into one reusable gate, referenced by id from a node or a pipeline stage. Present layers always run in the fixed order `validate → judge → review → approve`; the automated layers *filter and drive a bounded retry loop but never decide* — the only exit is human `approve`. On retry exhaustion it escalates to a human with the last critique attached; it never silently advances.

```yaml
apiVersion: swarmkit/v1
kind: Funnel
metadata: { id: consolidated-design-approval, name: Consolidated Design Approval }
validate:
  schema: schemas/consolidated-design.json    # deterministic; the judge never sees malformed input
  autocorrect: true
judge:
  skill: artifact-judge                        # a decision skill scoring against a rubric
  rubric: rubrics/consolidated-design.md
  threshold: 0.8                               # below this → a retry, not a rejection
  max_retries: 2                               # then escalate to a human, never drop
review:
  archetype: architect-reviewer                # optional heavyweight harness reviewer (Step 7)
  read_scope: [app:oms, app:web, app:mobile]
  route_back_at: high                          # findings >= this cause a retry; lower ones attach
approve:                                        # required — the only exit
  rules:
    - scope: design:approve
      roles: [oms-lead, web-lead, mobile-lead]
      quorum: all
  exclude_author: true
  min_distinct_approvers: 2
provenance: { authored_by: human, version: 1.0.0 }
```

Drop layers to taste: a `Funnel` with only `approve` is a plain multi-party sign-off.

→ [Funnel reference](../reference/funnel.md) · [Gate funnel design](../design-notes/gate-funnel.md)

## Step 7 — a coding harness as a node

Sometimes a node should be a real coding agent (Claude Code, opencode, Codex, Gemini CLI) that opens a repo and produces a diff — not a single model call. That is the **executor** seam. An archetype selects a harness with an `executor` block; everything else (governance, observability, the funnel it feeds) is unchanged:

```yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata: { id: architect-reviewer, name: Architect Reviewer (harness) }
role: worker
executor:
  kind: harness
  ref: claude-code            # swap for opencode / codex / gemini-cli
defaults:
  prompt: { system: "Investigate — verify the design matches the code. Read only." }
  iam: { base_scope: [app:read, kb:read] }
provenance: { authored_by: human, version: 1.0.0 }
```

Harnesses are **data**: a declarative `ExecutorAdapter` (`adapter.yaml`) interpreted by one engine — no per-harness Python. A harness runs in an ephemeral git worktree by default (produces a diff, never integrates); out-of-grant permissions relay to a human inbox mid-run (`swarmkit review`) and resume; an opt-in container sandbox adds real isolation. Authoring a new harness is writing one `adapter.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: ExecutorAdapter
metadata: { id: echo-harness, name: Echo Harness }
spec:
  launch: { command: [echo-harness, "{task.statement}"] }
  stream: { format: jsonl }
  event_map:
    - when: { type: done }
      emit:
        - event: result
          with: { status: success, output: "$.text" }
provenance: { authored_by: human, version: 0.1.0 }
```

→ [Executor adapter reference](../reference/executor-adapter.md) · [Executor abstraction](../design-notes/executor-abstraction.md) · [Authoring a harness adapter](https://github.com/delivstat/swarmkit/blob/main/docs/guides/authoring-harness-adapters.md)

## Step 8 — a pipeline as data

A single topology run is bounded — minutes, one team, one concern. A real delivery pipeline spans weeks, many teams, external events (Jira, CI, SAST), and human gates. That is a **StageGraph**: an ordered set of bounded `stages[]` that a **controller** sequences as a **saga**.

Each stage kicks a topology run; the runtime only ever runs the bounded stages. The **controller owns the durable state** — current stage, held locks, pending gate, attempt counts — reacts to events with dedup + reconciliation, manages contract locks, unwinds on cancel, and drives the runtime through a small, **domain-neutral** contract keyed on an opaque `correlation_id` (never a business id). The reference controller lives in the [SDLC example](https://github.com/delivstat/swarmkit/tree/main/examples/sdlc-pipeline), not in the runtime — a different pipeline is new data, not new controller code.

```yaml
apiVersion: swarmkit/v1
kind: StageGraph
metadata: { id: sdlc-full, name: SDLC full-lifecycle pipeline }
stages:
  - id: intake
    topology: oms-intake
    when: [requirement.created]            # entry event(s) — NOTE: `when`, not `on:`
    success: design.kickoff                # signal emitted on clean completion → next stage's `when`
  - id: design
    topology: consolidated-design
    when: [design.kickoff]
    locks: [oms-inventory, oms-web]         # integration contracts, all-or-none, held through approval
    gate: consolidated-design-approval      # a Funnel id; the run parks on it
    success: design.approved
    release_locks_on: design.approved
    compensation: oms-compensate-design     # unwind topology if the requirement cancels
  - id: build
    topology: oms-build-harness
    when: [design.approved]
    success: build.ready-in-qa              # EXTERNAL event (CI) — the controller waits, never fabricates
  # … sit → pt → security-review → deploy → support-handover
loops:                                      # cross-stage edges: the defect cycle
  - { when: defect.raised, to: build }
  - { when: defect.fixed,  to: sit }
provenance: { authored_by: human, version: 1.0.0 }
```

!!! warning "`when`, not `on:`"
    A bare `on:` key is coerced to the boolean `true` by YAML 1.1 parsers. The schema uses `when` for both a stage's entry events and a loop's trigger. Always use `when`.

A stage's `locks` reference **Contract** artifacts — making a lock id a checked, pickable vocabulary instead of a free-form string a typo could silently fork:

```yaml
apiVersion: swarmkit/v1
kind: Contract
metadata: { id: oms-web, name: OMS ↔ Web order API }
parties: [oms, web]                         # >= 2 app ids — an interface *between* apps
provenance: { authored_by: human, version: 1.0.0 }
```

→ [StageGraph reference](../reference/stage-graph.md) · [Contract reference](../reference/contract.md) · [Pipeline controller](../design-notes/pipeline-controller.md) · [Orchestration provider seam](../design-notes/orchestration-provider-seam.md) · [Tutorial 16: Pipelines & Contracts](../tutorials/16-pipelines.md)

## Step 9 — multi-party human approval

A pipeline's gates resolve to real people through a **RoleRegistry**. A gate's `approve` rules name roles; the registry maps roles to member identities and the scopes they confer; scopes reserved for human identity can never be held by an agent.

```yaml
apiVersion: swarmkit/v1
kind: RoleRegistry
metadata: { id: sdlc-roles, name: SDLC role registry }
roles:
  - { id: oms-lead,    members: [alice], scopes: [design:approve] }
  - { id: infosec-lead, members: [dana], scopes: [security:approve] }
  - { id: eng-manager, members: [grace], scopes: [release:approve] }   # human-only prod authority
  - { id: cio,         members: [heidi], scopes: [release:approve] }
```

The **ApprovalPolicy** (the `approve:` block — embedded config, not a standalone artifact) has two independent axes: *which roles signed* (`quorum: all | any | { k-of: N }`) and *how many distinct humans signed* (`min_distinct_approvers`, the four-eyes floor). A dual-hatted person can satisfy two roles but never two distinct-approver slots.

→ [Role registry reference](../reference/role-registry.md) · [Approval policy reference](../reference/approval-policy.md) · [Multi-party approval](../design-notes/multi-party-approval.md)

## Step 10 — triggering

Pipelines advance on the outside world. A **Trigger** is an external event source that either starts a topology or emits an event onto a running pipeline. A signed CI webhook that pushes a stage forward:

```yaml
apiVersion: swarmkit/v1
kind: Trigger
metadata: { id: ci-build-ready, name: CI build-ready webhook }
type: webhook
targets:
  - pipeline: oms-pipeline
    emit: build.ready-in-qa
    correlation_id: $.correlation_id       # opaque handle extracted from the JSON body
config:
  auth: { method: hmac, credentials_ref: CI_WEBHOOK_SECRET }
```

The `swarmkit serve` HTTP front door receives it: the receiver validates the HMAC, extracts the opaque `correlation_id`, and emits the event onto the pipeline ingress. Emitting a pipeline event is a **reserved, human/authorized-only authority** — `pipeline:advance` and `pipeline:skip` are in the reserved scope set and gated by the policy engine, audited, and deny with 403 otherwise. No agent can advance a pipeline by talking.

→ [Trigger reference](../reference/trigger.md) · [Pipeline triggering](../design-notes/pipeline-triggering.md) · [Serve mode](../reference/serve.md) · [Tutorial 12: Triggers & Canary](../tutorials/12-triggers-canary.md)

## Step 11 — serve, observe, evolve

Ship it behind the server, watch it, and let it tell you how to grow:

- **Serve.** `swarmkit serve` exposes topologies as async jobs with SSE streaming, an MCP endpoint, pluggable auth (API key / JWT-JWKS), webhook triggers, and canary version routing with auto-promotion. → [Serve mode](../reference/serve.md) · [Tutorial 11: Serve & HTTP API](../tutorials/11-serve-api.md)
- **Observe.** Every run is a trace of agent-step spans with token counts. `swarmkit trace <run>`, `swarmkit status`, `swarmkit logs`, `swarmkit why <run>` (LLM post-mortem), `swarmkit ask`. OpenTelemetry export is built in. → [Telemetry](../reference/telemetry.md) · [Human interaction model](../design-notes/human-interaction-model.md)
- **Remember.** Workspace memory lets agents carry insight across conversations (local JSON or a GBrain backend). → [Workspace memory](../reference/workspace-memory.md) · [Tutorial 9: Conversations & Memory](../tutorials/09-conversations-memory.md)
- **Grow.** The runtime records capability gaps (`swarmkit gaps`); you author the missing skill through conversation (`swarmkit edit`), test it, and publish — human-approved at every step. → [Skill authoring](../design-notes/topology-skill-authoring.md) · [Tutorial 13: Authoring & Review](../tutorials/13-authoring-review.md)

## The worked example — the SDLC pipeline

Everything above is assembled, end to end, in [`examples/sdlc-pipeline`](https://github.com/delivstat/swarmkit/tree/main/examples/sdlc-pipeline): a complete software-delivery lifecycle — **intake → design → build → sit → pt → security-review → deploy → support-handover** — carrying three multi-party human gates, integration-contract locks held through approval, per-stage compensations, a cross-stage defect loop, a harness build/review node, webhook triggering, and the reference saga controller (with a Temporal adapter for production). It is the reference for how the pieces fit.

- **Watch it.** The [captioned video walkthrough](../sdlc-example/index.html) tours every artifact and runs a stage end to end.
- **Run it.** `just demo-sdlc` drives one requirement through all eight stages + three gates deterministically via the reference controller; `just demo-sdlc-stage-run` runs a single gated stage.
- **Read it.** The [SDLC pipeline example design note](https://github.com/delivstat/swarmkit/blob/main/design/details/sdlc-pipeline-example.md) is the build-order narrative (slices 1–9) and the automation map (which stages are agent-run vs. human-gated).

## Validate everything

The whole point of topology-as-data is that a swarm is checkable before it runs:

```bash
swarmkit validate .                    # resolve + type-check the workspace, print the tree or errors
python examples/sdlc-pipeline/validate_library.py   # validate every artifact in a library
```

The resolver rejects a lock that names no contract, an approval rule whose scope no role confers, a stage that kicks an unknown topology, and any artifact that fails its schema. Validation is the fast feedback loop; a live `swarmkit run` is the confidence loop — do both.

→ [`swarmkit validate` reference](../reference/cli.md)

## Reference index

- **Artifacts:** [topology](../reference/topology.md) · [workspace](../reference/workspace.md) · [archetypes](../reference/archetypes.md) · [skills](../reference/skills.md) · [funnel](../reference/funnel.md) · [stage-graph](../reference/stage-graph.md) · [contract](../reference/contract.md) · [role-registry](../reference/role-registry.md) · [trigger](../reference/trigger.md) · [executor-adapter](../reference/executor-adapter.md) · [approval-policy](../reference/approval-policy.md)
- **Runtime seams:** [governance provider](../design-notes/governance-provider-interface.md) · [model provider](../design-notes/model-provider-abstraction.md) · [executor abstraction](../design-notes/executor-abstraction.md) · [orchestration provider seam](../design-notes/orchestration-provider-seam.md)
- **Operate:** [CLI commands](../reference/cli.md) · [serve](../reference/serve.md) · [telemetry](../reference/telemetry.md) · [notifications](../reference/notifications.md) · [env config](../reference/env-config.md)
- **Learn by doing:** the [16-level tutorial ladder](../tutorials/index.md) walks the same arc one runnable step at a time.
