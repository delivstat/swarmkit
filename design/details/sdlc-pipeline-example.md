# SDLC pipeline example workspace

A flagship example (`examples/sdlc-pipeline/`) that models an end-to-end software-delivery
pipeline for a real-world D2C retailer: three applications — Order Management System (OMS),
Shopping Website + PWA, Mobile app — each owning its own resources (code, configs, infra,
compliance stacks), coordinated through a single SwarmKit workspace with human-approved gates
at every sign-off.

It is the first example that exercises all three pillars *and* the separation-of-powers
governance model (§8) at once: topology-as-data, skills-as-extension, and
growth-through-authoring, gated by structural human approvals.

## Goal

Show that a real, multi-team SDLC — with genuine access separation between app teams and
real human accountability at every gate — can be **composed as data** in one SwarmKit
workspace, with an **agent doing the first-pass toil at every step** and humans owning every
decision. The demo walks one requirement from intake to a deploy-ready package, with real
human approvals along the way.

## Non-goals

- Not full automation. Humans own every gate; agents draft, analyse, and coordinate between
  gates. We do not remove a single accountable human decision.
- Not a live production deployment. Actual coding and deployment happen separately; the
  example orchestrates **up to** the build/deploy boundary and hands off (to a harness against
  demo repos, or to a human). Infra-touching steps (SIT env, PT rig, prod deploy) are backed
  by mock MCP servers in the shipped example.
- Not a fleet. This is one workspace, not one-instance-per-team (see "Why one workspace").
- Not simulated approvers. The demo uses real, distinct human identities resolving real tasks.
- **Not a workflow/BPM engine.** SwarmKit owns bounded per-stage runs + governance gates, not
  the weeks-long cross-system state machine. That belongs to an external controller (see
  "Orchestration boundary"). Same split as Minder: the application owns logic/state, agents do
  determination.

## Why one workspace (not one instance per team)

The fleet / control-plane model is for swarms that are **independently operated** and only
*roll up* telemetry to a panel. This pipeline is the opposite: one requirement threads through
many teams and the value lives at the seams — the **consolidated design**, **SIT** (e2e
business flows), and **PT** (exposed services + cross-app regression). If teams were separate
instances, those cross-cutting steps would have to federate across instance boundaries
(cross-instance messaging, shared pipeline state, multi-panel approval) — hand-built
distributed coordination that a single workspace provides for free.

Decision: **one workspace, one correlated audit trail, one consolidated design.** Team
*separation* is IAM scoping *inside* the workspace, not instance boundaries. (Note: "one
workspace" does not mean "one mega-run" — see "Orchestration boundary". The pipeline is a
series of per-stage runs in that one workspace, correlated by `requirement_id`.)

## Access separation (inside one topology)

Skills and resources are granted to **identities, scoped to an app**:

- `oms-*` agents bind to `app:oms` resources only (git repo, DB, Jira project, config store).
  An OMS agent cannot call the Web repo tool — the skill was never granted to its identity.
  Structural (§8.7), not prompt hygiene.
- `web-*` → `app:web`; `mobile-*` → `app:mobile`.
- **Cross-cutting agents** (`integration-architect`, `sit-qa`, `pt-engineer`) get **read**
  across all three apps but **write** only to the shared artifacts (consolidated design, e2e
  suite, PT plan). That shared surface is the only place the walls come down — exactly the
  three cross-cutting points.

## Artifact inventory (API shape)

Archetypes: `business-analyst`, `solution-architect` (per app), `integration-architect`,
`developer` (harness executor, per app), `qa-engineer` (per app), `sit-qa`, `pt-engineer`,
`release-coordinator`, `support-engineer`, `release-orchestrator`.

Approver roles (data, in the role registry — extend freely): `oms-lead`, `web-lead`,
`mobile-lead`, `infosec-lead`, `qa-lead`, `pt-lead`, `eng-manager`, `cio`.

Outside SwarmKit: a **`controller`** — holds per-requirement state, listens for enterprise events
(mock driver in the example), and kicks the next stage's SwarmKit run. It is a **reusable
reference component driven by a declarative stage-graph** (data), *not* per-example code (see
"Reusable, showcase-class artifacts"). Not an agent; not part of the topology.

Topology (delegation tree; pipeline *sequencing* is triggers + gates, not hardcoded flow):

```
release-orchestrator (root)            — owns pipeline state; advances on triggers + gates
├── intake            (business-analyst) — ingests BRD, impact analysis → affected apps
├── oms-architect | web-architect | mobile-architect  — per-app first-draft designs
├── integration-architect              — synthesizes the CONSOLIDATED design (cross-app read)
├── oms-dev | web-dev | mobile-dev (harness) + per-app qa  — build + unit/regression (scoped)
├── sit-qa                             — e2e business-flow tests, frontend→backend, cross-app
├── pt-engineer                        — perf of exposed services + cross-app regression
└── release-coordinator + support-handover
```

The 16 steps are **not** hardcoded control flow, and — critically — they are **not** one giant
SwarmKit run either. See the orchestration boundary below.

## Orchestration boundary: SwarmKit determines + governs; a controller sequences

Same split as Minder (`feedback_llm_language_code_doing`): the application owns logic and state;
agents do determination only. An enterprise SDLC's real state machine is driven by events that
live in *other systems* (Confluence/Jira transitions, CI build results, Git merges, SAST/DAST
webhooks), so making SwarmKit's internal trigger scheduler the source of truth for them would be
fiction — and would quietly turn SwarmKit into a weeks-long BPM engine, which is not a pillar.

Draw the boundary at **durable cross-system waits**:

- **Inside SwarmKit — each pipeline *stage* is one bounded topology run**: the stage's agents
  (determination) **plus that stage's human gate**. The run parks on the gate until humans
  resolve, then completes. Bounded, resumable, and it *includes* the approval — the gate + audit
  + IAM scoping is exactly SwarmKit's strength. No stage run lives longer than draft → gate → done.
- **Outside SwarmKit — a thin `controller`** holds the weeks-long state + `requirement_id` and
  reacts to **real enterprise-system webhooks** by kicking the next SwarmKit run. This is the
  Minder "application" role: a small app, not logic-in-agents. Stage-advancing triggers are
  **external**; intra-stage flow (agent → gate → agent) is **internal**.
- **Audit** is assembled **across** per-stage runs by `requirement_id` correlation (SwarmKit
  already aggregates across runs for the fleet usage/eval/audit rollups) — per-stage traces plus
  one correlated view, cleaner than a multi-week span that never closes.
- **The multi-party approval gate stays a SwarmKit governance primitive** (state of record +
  append-only audit + reserved human scopes); the controller only *observes* resolution to advance.
- A **Knowledge Curator** owns the shared KBs so every stage reads a consistent picture.

Stage transitions (each a real-world event the controller listens for): `requirement.created` →
intake; `design.approved-by-all` → build; `build.ready-in-qa` → sit; `defect.raised` →
defect-triage; `defect.fixed` → targeted re-test + regression.

For the **shipped example**, the controller's external events are faked by a small local driver
script (no Jira/CI needed to run the demo), but the event-source seam is explicit: swap the mock
driver for real webhooks and it is a real deployment. The example teaches the hybrid, not a toy.

## Reusable, showcase-class artifacts

This is not throwaway example glue — it ships a **reusable kit** that another pipeline (or
another org's SDLC) can adopt by configuration. Reusability rides on the primitives that
*travel*: skills, archetypes, and declarative harness adapters (the only extension primitives),
plus a data-driven controller. Everything below is versioned, documented, and library-publishable.

- **Archetypes (roles).** `business-analyst`, `solution-architect`, `integration-architect`,
  `developer` (harness), `qa-engineer`, `sit-qa`, `pt-engineer`, `release-coordinator`,
  `support-engineer`, `release-orchestrator` — a reusable SDLC role library.
- **Skills.** The determination + coordination capabilities, each a standalone skill:
  impact-analysis (decision), consolidated-design synthesis (coordination), defect-triage
  (decision), test-plan generation (capability), code-review determination (decision),
  PT analysis (decision), and the **multi-party approval request** (coordination) — reusable in
  any workspace, not just this one.
- **Governance: multi-party approval.** Enforcement stays in the `GovernanceProvider` / policy
  engine (invariant 3 & 6 — reserved human scopes are structural, an agent cannot self-approve);
  the reusable surface is the **role-registry schema** + the **per-gate approval-policy schema**
  (all / any / k-of). A skill lets an agent *request* the gate; the engine *enforces* it.
- **Controller + stage-graph schema.** The controller is a reference component that interprets a
  declarative **stage-graph artifact** (stages, their SwarmKit topology, entry/exit events,
  gates) — so a new pipeline is *data*, not new controller code. Keeps topology-as-data spirit at
  the sequencing layer.
- **Harness adapters.** Bundled declarative `adapter.yaml` for `claude-code` and `opencode`
  (executors-are-data — no per-harness Python), reusable by any harness-executor agent.

The example workspace is then a thin *composition* of these reusable parts + org-specific data
(the three apps' KBs, the role registry's members, the stage-graph). That composition is the demo;
the parts are the product.

## Harness showcase (claude-code / opencode)

The `developer` archetype is a **harness executor** (invariant 2: `harness` is an executor kind
alongside `model`). Per app, in a container sandbox scoped to that app's repo, the harness
(claude-code or opencode, selected by config — never hardcoded) opens a session, implements the
approved design, runs unit + regression, and **produces a candidate diff**. That diff is the
determination artifact; the **code-review gate** (per-app lead) and the team's external merge/CI
finalize it — consistent with "actual coding + deployment happen separately". This is the first
example to exercise a session-holding, diff-producing harness end-to-end, and it swaps harnesses
by changing one `executor` field, proving the abstraction.

## New capability: configurable multi-party approval sets

Today's gate (`ReviewGate`) is one question → one resolver. This example needs an **approval
set**: a gate that stays open until the **roles it requires** have each approved, by **distinct
human identities**. The required roles and the quorum rule are **per-gate configuration**
(data), not hardcoded — different gates need different approvers, and that must be tunable
without touching code (the "everything configurable" invariant).

Two artifacts:

1. **Role registry** (workspace-level IAM data) — maps each role to a governance scope and the
   human identities that hold it:
   ```yaml
   roles:
     - id: oms-lead        scope: design:approve   members: [alice]
     - id: web-lead        scope: design:approve   members: [bob]
     - id: mobile-lead     scope: design:approve   members: [carol]
     - id: infosec-lead    scope: security:approve members: [dana]
     - id: qa-lead         scope: testplan:approve members: [erin]
     - id: pt-lead         scope: perf:approve     members: [frank]
     - id: eng-manager     scope: release:approve  members: [grace]
     - id: cio             scope: release:approve  members: [heidi]
   ```

2. **Per-gate approval policy** — each gate declares one or more approval *rules*, each a group
   of roles plus a quorum mode. v1 supports all three modes:
   ```yaml
   gate: consolidated-design-approval
   rules:
     - { roles: [oms-lead, web-lead, mobile-lead], quorum: all }   # every app lead
     - { roles: [infosec-lead],                    quorum: all }   # infosec required
     - { roles: [arch-reviewer-a, arch-reviewer-b, arch-reviewer-c], quorum: { k-of: 2 } }
   ```
   - `all` — every role in the group must approve.
   - `any` — one role in the group suffices (e.g. either on-call SRE).
   - `k-of: N` — any N distinct role-holders in the group (e.g. any 2 of a reviewer pool).

   A gate advances only when **all** its rules are satisfied. Modelling groups explicitly (rather
   than per-role quorum) is what makes `k-of` well-defined.

Semantics:
- The gate emits **one task per required role**, routed to that role's members.
- Resolution records each approver's identity + role + timestamp in the append-only audit log.
- The advancing trigger fires only when quorum is met across **distinct** identities (one
  person holding two roles counts once per role, but cannot self-satisfy a two-identity rule).
- Each resolution is one of three outcomes: `approve` / `changes-requested` / `reject` (see
  "Rework + re-approval").

This is the one genuinely new governance primitive; everything else composes existing pieces.
It is built first (see test plan) and is generally useful beyond this example.

### How gates wait (LangGraph checkpoints)

A gate compiles to a **LangGraph `interrupt()` backed by a checkpointer**: on reaching the gate,
the run persists its full state to the DB and the invocation *returns* — no process parked, no
compute burned. Resume is a later `Command(resume=…)` on the persisted `thread_id`. A multi-week
wait costs one DB row. For multi-party, the checkpoint carries the **running tally** ("2 of 4
approved, waiting on infosec + cio") durably across weeks and across people — the gate node
re-interrupts after each approval with the updated tally persisted, proceeding only at quorum.

This is why the wait is *not* the durability risk (see Constraints): the residual concern is
state-schema evolution over long horizons, which per-stage runs mitigate. Human resumes come
through the gate (LangGraph); external-system resumes ("build ready", "defect fixed") come
through the controller — complementary, both async, both persisted.

## Rework + re-approval

Two loops at two levels — they look alike but live in different places.

**Intra-stage rework loop (inside one SwarmKit run).** On a `changes-requested` resolution, the
reviewer's comments (free-text on the gate) flow back to the **drafting agent**, which revises the
artifact incorporating them, and the gate **re-opens** — same run, the checkpoint persisting the
revise → re-review cycle. Repeats until approved. The whole episode — every cycle, comment, and
approval — stays in **one self-contained audit sub-trace**. Applies uniformly to any artifact:
design, code (comments → dev agent revises the diff), test plan, defect analysis.

Per-gate policy `on_revision` decides what a revision does to prior approvals:
- `reset_all` (default) — a revised artifact invalidates prior approvals; all required roles
  re-approve. Honest for a single shared document (a change to the integration section affects
  every team).
- `reconfirm_changed` — only roles whose concerns were addressed re-review; unaffected prior
  approvals carry or get a lighter re-confirm task.
- *(future)* section-scoped approval — an OMS-only change re-triggers only `oms-lead`.

**Cross-stage defect loop (controller-driven).** Steps 7–8's cycle — SIT finds a defect → back to
build → fix → re-test + regression — is *not* rework inside one run; it spans stages and is fired
by external events. The controller owns it: `defect.raised` kicks a `defect-triage` run (dev-agent
first analysis + candidate fixes), `defect.fixed` kicks a `re-test` run (targeted test +
regression) — each a fresh bounded run, all correlated by `requirement_id`. The defect's history
is the sequence of correlated runs.

### Where each gate's approvers land (all configurable)

| Gate | Required roles (example config) |
| --- | --- |
| Consolidated design | oms-lead, web-lead, mobile-lead, infosec-lead |
| Test plan | qa-lead |
| Performance plan | pt-lead |
| Code review (per app) | that app's lead |
| SIT sign-off | qa-lead |
| PT sign-off | pt-lead |
| Final release sign-off | eng-manager, cio |
| Prod deploy | eng-manager, cio (human-only scope) |

## Automation map (agent-first, human-gated)

| Step | Automation | Gate |
| --- | --- | --- |
| Requirement handover | intake → impact analysis + kicks per-app architects → first-draft designs before a human touches it | architects review drafts |
| Consolidated design | integration-architect fixes integration patterns (payloads, channels, integration type) at design time | app leads + infosec-lead |
| Test plan | test-plan agent drafts cases from BRD + consolidated design | qa-lead |
| Build | harness dev agents (claude-code / opencode, sandboxed, scoped repo) implement the design → candidate diff + unit/regression | code review (per-app lead) |
| Defect | dev-agent produces first analysis + candidate fixes + clarifying query to QA | human dev reviews the analysis |
| QA / SIT | runs new + regression on build-ready; re-runs targeted test + regression on defect-fixed | qa-lead signs off SIT |
| PT | pt-engineer runs perf on exposed services + cross-app regression; PT plan drafted for approval | pt-lead (plan + sign-off) |
| Final sign-off | release-coordinator assembles the package + release notes | eng-manager + cio |
| Deploy | deploy package promoted | eng-manager + cio; prod deploy scope is human-only |

## Knowledge bases (Curator-owned)

Requirements/BRD · per-app architecture · compliance (DORA/SAST/DAST/InfoSec) · **consolidated
design (integration patterns)** · per-app codebase · test-case + regression suite · defect log ·
decisions/ADR (audit trail) · env/infra inventory · runbook/support handover.

Refreshed **per requirement**: BRD, consolidated design, test cases, defects, decisions,
runbook. Mostly static (edited on change): app architecture, compliance policy, env inventory.

## Constraints

- Multi-party approval set is new (build first).
- The multi-week *wait* is **not** a durability risk: gates are LangGraph interrupts backed by a
  checkpointer, so a parked run is a DB row, not a held process (see "How gates wait"). The real
  residual risk is **checkpoint state-schema evolution** over long horizons — a checkpoint pins
  the graph definition it was created against, so mid-flight topology changes make old-checkpoint
  resume fragile. Per-stage runs mitigate this (each uses the current definition and closes).
- Real, distinct human approvers in the demo (not simulated).
- Build/deploy stop at the boundary; harness runs against demo repos, infra steps mocked.
- Deterministic (transitions, gate-counting) vs LLM (drafting, analysis, synthesis) split is
  explicit. MCP servers (Confluence/Jira/Git/CI/SAST/monitoring) are configured, never coded
  per-vendor — no hardcoded provider.

## Test plan

- **Approval set (unit):** all three quorum modes — `all` (every role), `any` (one suffices),
  `k-of: N` (any N distinct role-holders); quorum counted across distinct identities; duplicate
  approver rejected; a gate advances only when every rule is satisfied; `changes-requested`
  re-opens per `on_revision` policy (`reset_all` vs `reconfirm_changed`); audit records each
  approval with identity + role.
- **Role-registry + approval-policy schema:** valid configs parse; a rule referencing an unknown
  role is rejected; `k-of` with N > group size is rejected.
- **Harness adapter (unit):** the `developer` archetype resolves to the configured harness
  (`claude-code` / `opencode`) via `executor` field; swapping the field swaps the adapter with no
  other change (proves executors-are-data).
- **Schema validation:** every topology/archetype/skill/trigger/workspace + stage-graph YAML
  validates against the canonical schemas.
- **Controller stage-graph (unit):** each external event advances the correct stage; the
  cross-stage defect loop re-kicks build/re-test on `defect.raised`/`defect.fixed`; runs correlate
  by `requirement_id`.
- **Pipeline dry-run (integration):** one requirement through intake → consolidated design →
  build (harness against a demo repo) → SIT (mock) → PT (mock) → deploy package, with mock MCP
  servers and scripted gate resolutions; asserts one correlated audit trail, correct per-app
  scoping (an OMS agent denied a Web resource), and consistent KB state.

## Demo plan

`just demo-sdlc` (or a runnable script under `examples/sdlc-pipeline/`): submit a sample BRD,
watch intake produce impact analysis + per-app first-draft designs, resolve the multi-party
design gate as distinct identities, let the build/SIT/PT stages run against mocks, and produce
a deploy-ready package — with the full audit trail printed. Terminal transcript in the PR body.

## Build order (proposed slices)

1. Multi-party approval set (governance) — role-registry + approval-policy schemas, all three
   quorum modes (`all`/`any`/`k-of`), `on_revision` policy + tests. The enabling primitive.
2. Reusable archetype library (SDLC roles) + skills (impact-analysis, synthesis, defect-triage,
   test-plan, code-review, PT) as standalone library artifacts.
3. One-app (OMS) intake → design → approval as **one bounded stage run**, mock MCP — proves
   scoping + gates + the agent-determination-only shape.
4. The `controller` + **stage-graph schema**: data-driven sequencing, per-requirement state, a
   mock event driver correlating runs by `requirement_id`. Establishes the reusable boundary.
5. Consolidated design across all three apps (the cross-cutting synthesis).
6. Harness build (bundled `claude-code`/`opencode` adapter, sandboxed, candidate diff) +
   code-review gate against a demo repo — the executor showcase.
7. SIT + PT (cross-app) with mock rigs; defect loop (controller-driven re-test triggers).
8. Deploy package + support handover; full `just demo-sdlc`.
