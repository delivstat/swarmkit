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

## System at a glance

One requirement is a **saga**. The controller advances it stage by stage on external events; each
stage is a bounded SwarmKit run where agents draft, a gate funnel (validate → judge → reviewer)
filters, and a multi-party human approval set signs off; every run is correlated by
`requirement_id` into one audit trail. Three layers, cleanly separated:

- **Sequencing (outside SwarmKit):** the controller + a data stage-graph — reacts to enterprise
  events, holds weeks-long state, contains no logic-in-agents.
- **Determination (inside, per stage):** archetype agents (some harness executors) produce
  artifacts, scoped by per-app IAM so teams stay walled except at the shared surface.
- **Governance (inside, per artifact):** the gate funnel + multi-party human approval, all
  append-only audited; humans own every reserved-scope decision.

The document is in two halves: the **core model** (roles, boundary, gates, KBs — one requirement)
and **operating at scale** (many requirements in flight: contention, failure, DORA, the human
surface). Read the first for *how it works*, the second for *whether it survives a real org*.

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
`release-coordinator`, `support-engineer`, `release-orchestrator`. Reviewer archetypes (harness
executors, for investigative outside review): `architect-reviewer`, `security-consultant`.

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
  `developer` (harness), `architect-reviewer` + `security-consultant` (harness reviewers),
  `qa-engineer`, `sit-qa`, `pt-engineer`, `release-coordinator`, `support-engineer`,
  `release-orchestrator` — a reusable SDLC role library.
- **Skills.** The determination + coordination capabilities, each a standalone skill:
  impact-analysis (decision), consolidated-design synthesis (coordination), defect-triage
  (decision), test-plan generation (capability), code-review determination (decision),
  PT analysis (decision), **artifact-judge** (decision — the LLM-as-judge gate, one reusable
  skill parameterised by rubric per artifact), and the **multi-party approval request**
  (coordination) — reusable in any workspace, not just this one.
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

**Harness beyond coding — reviewer/consultant.** The `architect-reviewer` and
`security-consultant` archetypes are *also* harness executors, but for **investigative outside
review** rather than authoring. Given read-scoped access to the repo + KBs, the harness opens a
session and *investigates* — cross-checks the consolidated design against the actual code and
integration points, hunts for security gaps against the compliance KB — and returns findings.
This is layer 3 of the gate funnel, and it shows the harness is not just a coder: a stateless
model call judges the text it is handed; a harness reviewer goes and looks.

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

## Gate layers: every artifact is judged before a human sees it

Each artifact (design, consolidated design, test plan, diff, defect analysis, PT plan, release
package) passes a **quality funnel** — cheap → expensive → human — so humans only ever review
drafts that already cleared automated checks. SwarmKit supports this natively via governance
decision skills + structured-output validation (`project_governance_decision_skills`,
`feedback_structured_output_priority`); we make it standard on every artifact.

1. **Structured-output validation (deterministic).** Shape/schema correctness with field-level
   auto-correction (the Rynko pattern). No LLM. Eliminates shape hallucination before any judge.
2. **LLM-as-judge gate (governance decision skill).** A rubric-scored critique of the artifact
   against its acceptance criteria (design vs BRD; test plan coverage vs design; diff vs design +
   lint/test signal). On fail → the critique flows back to the drafting agent as an **auto-retry
   loop** (bounded), before a human is ever paged. This is the judicial pillar (§8).
3. **Harness reviewer (heavyweight gates only).** An *investigative* outside review by a harness
   executor (see reviewer archetypes) — it opens the repo + KBs, cross-checks the artifact
   against real code/architecture, and produces findings. Used at the consolidated-design and
   pre-release gates, where a text-only judge is not enough.
4. **Human multi-party approval (binding).** The reserved-scope sign-off. It sees only artifacts
   that passed 1–3, with the judge score + reviewer findings attached as context — so humans
   review *quality* drafts and spend their attention on judgement, not defect-hunting.

Non-negotiable: layers 1–3 are **advisory** — they gate *advancement to human review* and drive
the retry loop, but they **never** substitute for the human decision on a reserved scope
(invariant 6). An artifact cannot reach a human gate *without* passing the judge; it can never
*bypass* the human gate by passing it.

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

---

# Operating at scale

Everything above is the **unit** — one requirement. A real org runs dozens concurrently over
weeks, and that is where the design is actually tested. This half does not pretend the hard
problems vanish.

## Many requirements in flight (contention)

Dozens of requirements share three codebases, one shared design surface, and a few scarce
approvers. The hard problems are all contention:

- **Shared-surface contention (decided: lock the integration contract per requirement).** The
  shared integration contract is a **locked resource**. A requirement acquires the lock on the
  specific contract(s) it touches when its consolidated-design stage begins, holds it through
  design approval (the new contract version is committed), then releases; other requirements
  needing the same contract **queue** behind it. Cross-cutting design work is thereby serialised
  per contract — correct and simple. Locks are **per-contract (per app-pair), not global**, so
  unrelated requirements proceed in parallel. A held lock is visible on the board and carries the
  same SLA/escalation as a gate, so a stuck lock is surfaced, not silent. Non-shared artifacts
  (per-app code, test cases) stay optimistic/versioned — only the shared contract locks.
- **Codebase drift.** A design approved weeks ago was written against code that later requirements
  have since changed. The build stage **re-bases against HEAD and re-runs the judge** before a
  human sees the diff; a material drift re-opens the design gate.
- **Approver bottleneck.** eng-manager/cio gate *every* release and become the constraint.
  Mitigations are data, not code: batchable gates, delegation/`k-of` pools, and SLA + escalation
  (below). The pipeline should *surface* throughput per gate, not hide the bottleneck.
- **KB write concurrency.** The Curator serialises writes per KB; each stage run pins the KB
  versions it read (like the codebase), so a run sees a consistent snapshot.

v1 of the example demonstrates these mechanisms on 2–3 concurrent requirements; it does not claim
to have solved org-wide throughput.

## Time, failure, and cancellation

Weeks-long sagas fail and get abandoned; the happy path is the minority case.

- **Gate SLA + escalation.** Each gate carries an SLA; on breach the task escalates (next role up)
  and/or notifies — data on the gate. Prevents silent multi-week stalls.
- **Stage-run failure ≠ wait.** A wait is a persisted checkpoint (cheap). A *failure* — harness
  crash, provider outage, MCP unreachable — is different: the controller retries the stage run
  **idempotently**, and a poisoned stage surfaces to a human, never silently drops the requirement.
- **Cancellation + compensation.** A requirement can be withdrawn at any stage. The controller
  runs the stage-graph's declared **compensation** for stages already passed (revoke a draft, close
  open gate tasks, mark KB entries superseded). A saga needs an unwind path.
- **Webhook robustness.** External events duplicate, arrive out of order, or go missing. The
  controller dedupes on an **idempotency key** per (requirement, event) and periodically
  **reconciles** its state against the source systems — never trusting a single delivery.

## Governance payoff: DORA + pipeline observability

The correlated append-only audit is not just compliance evidence — it is the product's payoff for
a regulated retailer:

- **DORA + compliance out of the box.** Lead time, deployment frequency, change-failure rate, and
  MTTR fall directly out of per-requirement run correlation; DORA/SAST/DAST sign-offs are audit
  records, not spreadsheets. This is the headline for the target audience.
- **Pipeline-wide view.** Beyond one run's trace, operators need a **cross-requirement board** —
  every in-flight requirement and the gate/lock it sits at, plus throughput/bottleneck per gate.
  Decided in scope for v1; backed by a shared namespaced space (see "The board").

## Closing the loop: metrics drive pipeline improvement

The audit is not only a compliance record and a DORA report — it is the **measurement substrate for
improving the pipeline itself**, closing onto the third pillar (growth-through-authoring). This is
**not new machinery**: SwarmKit already has the growth loop — gap → proposal → human approval →
publish (`packages/control-plane` proposal queue) — and the **eval harness** (`eval-harness.md`,
implemented) as its "measure/test" gate (§12: gap → author → test → publish). The pipeline's own
metrics are simply another **signal** into that loop.

- **What is measured** (from the correlated audit + the controller's saga timeline): the four DORA
  keys — deployment frequency (deploy-stage completions), lead time (`requirement.created` → prod),
  change-failure rate, MTTR — plus SAST/DAST outcomes (compliance-gate audit records) and per-gate
  throughput/bottleneck (the board). *Caveat:* change-failure rate and MTTR need **prod signal**
  (incident/rollback data), which the example mocks; the rest fall straight out of correlation.
- **How it improves the pipeline** (human-gated): a metric that flags waste becomes a growth signal →
  a **proposal** a human approves, scored by the eval harness before publish. The design gate
  dominating lead time surfaces the approver bottleneck (tune quorum / batch); a stage with high
  change-failure rate → tighten its judge rubric or add a harness reviewer; a requirement class that
  always round-trips (payments → security) → author a pre-check skill so it is caught earlier.
- **Never autonomous.** The pipeline *proposes* changes to itself; humans approve. Changes to gates,
  topologies, or IAM ride the reserved human scopes (`topologies:modify`, `iam:modify`) — no agent can
  self-rewire the process, by construction.

In one line: **the pipeline that ships software also measures and improves how it ships software —
with a human approving every change to itself.**

## Human task surface

Humans are the scarce resource, so their experience is first-class:

- **Per-role queues.** A qa-lead sees one queue of pending gate tasks across *all* requirements —
  a query on their namespace in the shared board (see below), not a hunt through runs.
- **Notification routing.** Gate tasks notify via configurable channels — reuse Minder's
  channel-adapter model (`project_minder_channels`: Slack/Telegram/email), config-driven, no code
  per channel.
- **Context in the task.** Each task carries the artifact + judge score + reviewer findings +
  diff-since-last-approval, so a human decides in one place.

## The board: a shared, namespaced coordination space

Decided in scope for v1 — it is the operator's primary surface at scale. Rather than a bespoke
status DB, the board is a **shared, namespaced coordination space**: a GBrain-style knowledge graph
exposed over MCP (`reference_gbrain`: git-backed, hybrid search, self-wiring). Every team and
requirement writes under its **namespace** (`req/<id>`, `app:oms`, `role:qa-lead`), but it is one
common space — so separation *and* a single queryable org-wide view come from one substrate:

- **Write-scoping = the same IAM discipline.** A team/agent writes only its namespace; the common
  *read* space is the shared view. No new access model — it reuses the per-app scoping.
- **Derives the pipeline view.** In-flight requirements and the gate/lock each sits at, throughput
  and bottleneck per gate, held integration-contract locks — all queries over the space.
- **Derives the per-role queues.** A qa-lead's pending tasks across all requirements is a query on
  `role:qa-lead`. Org board and human task surface are the same substrate, different views.
- **Not the source of record.** Three distinct roles: the append-only **audit** is authoritative;
  the Curator **KBs** are the artifact store; the **board** is a live coordination/status surface
  derived from and annotating them.

This is a second reuse of an existing SwarmKit-adjacent building block (GBrain as a workspace MCP
server), not net-new infrastructure.

## What is framework vs what is example

This design straddles two layers. Decided: the **framework parts each land as their own design
note + PR** (each is independently useful and independently testable), with the example
*consuming* them. The five net-new capability notes:

1. `multi-party-approval` — role registry + per-gate approval policy (all/any/k-of) + `on_revision`.
2. `gate-funnel` — the reusable gate composition validate → judge → review → approve.
3. `pipeline-controller` — the controller + stage-graph schema + saga semantics (per-requirement
   state, contract locks, compensation, webhook idempotency/reconciliation).
4. `harness-reviewer` — the investigative (non-coding) harness reviewer pattern.
5. `task-surface-and-board` — per-role queues + notification routing + the shared namespaced board.

**Example content** (this workspace, its own PR): the SDLC archetype/skill library, the three
apps' KBs + mock MCP servers, the role-registry members, the stage-graph instance, and the demo.

The five capabilities are the early build-order slices precisely because the example cannot exist
without them.

## Growth (the third pillar, made real)

The intro claims growth-through-authoring; this is where it shows up rather than being asserted:

- A **recurring class of requirement** (e.g. "add a new payment provider") that keeps re-deriving
  the same design → gap-detection surfaces it → a human authors a reusable skill/playbook → the
  next such requirement starts from it. Gated authoring, per §12.
- A **fourth application** joins → author a new per-app archetype set + KB, register it in the role
  registry and stage-graph. The pipeline grows by authoring, no engine change.

---

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
- **Gate funnel (unit + integration):** structured-output validation auto-corrects a malformed
  field; the artifact-judge fails a below-rubric draft and the critique drives a bounded retry
  that then passes; a judged-pass artifact reaches the human gate with score + findings attached;
  and — the invariant — an artifact can neither reach a human gate without passing the judge nor
  bypass the human gate by passing it.
- **Harness reviewer (integration):** `architect-reviewer` (read-scoped) surfaces a planted
  design/code mismatch; `security-consultant` surfaces a planted compliance gap; findings land on
  the relevant gate.
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

## Build order (a program, not one feature)

**Status:** slices 1–4 shipped. 1 multi-party approval (v1.97.0), 2 reusable
archetype/skill library (#604), 3 the gate funnel as a first-class `kind: Funnel`
artifact (#608), 4 the one-app (OMS) bounded stage run — `StageRunner` +
in-node gate embedding + role-registry resolution, with the OMS example
(`examples/sdlc-pipeline/`, `just demo-sdlc`) proving IAM scoping + the gate funnel +
the agent-determination-only shape. The bounded stage runner is the deliberate
precursor to slice 5's data-driven controller. Next: slice 5 (controller + stage-graph
schema). Note: slice 4 corrected three slice-2 harness archetypes that used the
never-registered executor kind `harness`; the `kind: harness` + `ref:` shape the
archetype schema documents is not implemented by the runtime executor registry (which
registers concrete adapter kinds) — a follow-up for the executor abstraction.

This is a **program of features**, not a single PR — the framework capabilities (slices 1–3, 5)
each land as their own design note + PR per the mandatory workflow; the example workspace composes
them. The **first shippable increment / minimal lovable demo** is slices 1→4: one app, one
requirement, the full gate funnel + multi-party approval, mock MCP — enough to *see* the pattern
work end to end. Everything after widens coverage (three apps, harness build, cross-app test,
scale mechanisms).

1. Multi-party approval set (governance) — role-registry + approval-policy schemas, all three
   quorum modes (`all`/`any`/`k-of`), `on_revision` policy + tests. The enabling primitive.
2. Reusable archetype library (SDLC roles) + skills (impact-analysis, synthesis, defect-triage,
   test-plan, code-review, PT, **artifact-judge**) as standalone library artifacts.
3. The **gate funnel**: structured-output validation → artifact-judge decision skill (fail →
   bounded auto-retry to the drafter) → human gate, wired so every artifact is judged before a
   human sees it, and layers 1–3 can never bypass the human gate.
4. One-app (OMS) intake → design → judge → approval as **one bounded stage run**, mock MCP —
   proves scoping + the full gate funnel + the agent-determination-only shape.
5. The `controller` + **stage-graph schema**: data-driven sequencing, per-requirement state, a
   mock event driver correlating runs by `requirement_id`. Establishes the reusable boundary.
6. Consolidated design across all three apps (synthesis) + the `architect-reviewer` harness as
   layer-3 investigative review before the design gate.
7. Harness build (bundled `claude-code`/`opencode` adapter, sandboxed, candidate diff) +
   code-review gate against a demo repo — the executor showcase.
8. SIT + PT (cross-app) with mock rigs; `security-consultant` review pre-release; defect loop
   (controller-driven re-test triggers).
9. Deploy package + support handover; full `just demo-sdlc`.
10. Scale mechanisms: per-requirement contract locking, drift re-base, gate SLA/escalation,
    cancellation + compensation, webhook idempotency, and the shared namespaced board (org view +
    per-role queues) — demonstrated on 2–3 concurrent requirements.

## Decisions (resolved in review)

- **Shared-surface concurrency** — *lock the integration contract per requirement* (per-contract,
  not global; non-shared artifacts stay optimistic). See "Many requirements in flight".
- **Cross-requirement board** — *in scope for v1*, backed by a shared namespaced coordination
  space (GBrain-style MCP). See "The board".
- **Program split** — *separate design note + PR per framework capability* (five notes, listed in
  "What is framework vs what is example"); the example workspace is its own PR.

No open questions remain; the design is ready to decompose into the five capability notes.
