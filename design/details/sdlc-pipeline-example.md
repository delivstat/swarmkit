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

## Why one workspace (not one instance per team)

The fleet / control-plane model is for swarms that are **independently operated** and only
*roll up* telemetry to a panel. This pipeline is the opposite: one requirement threads through
many teams and the value lives at the seams — the **consolidated design**, **SIT** (e2e
business flows), and **PT** (exposed services + cross-app regression). If teams were separate
instances, those cross-cutting steps would have to federate across instance boundaries
(cross-instance messaging, shared pipeline state, multi-panel approval) — hand-built
distributed coordination that a single workspace provides for free.

Decision: **one workspace = one pipeline run = one audit trail = one consolidated design.**
Team *separation* is IAM scoping *inside* the workspace, not instance boundaries.

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

The 16 steps are **not** hardcoded control flow. The pipeline is a state machine expressed as
**triggers + durable state + human gates**:

- Triggers move work between stages: `requirement.created` → intake; `design.approved-by-all`
  → build; `build.ready-in-qa` → sit; `defect.raised` → defect-triage; `defect.fixed` →
  targeted re-test + regression.
- Human gates (review-queue tasks) sit between stages; the run parks on a checkpoint until
  they resolve.
- A **Knowledge Curator** owns the shared KBs so every stage reads a consistent picture.

## New capability: multi-party approval sets

Today's gate (`ReviewGate`) is one question → one resolver. This example needs an **approval
set**: a gate that stays open until **K distinct human identities** holding a scope have each
approved. Example: `design:approve` held by `oms-lead`, `web-lead`, `mobile-lead`,
`infosec-lead`; the consolidated-design gate emits four tasks and advances only when all four
resolve. This is the one genuinely new governance primitive; everything else composes existing
pieces. It is built first (see test plan) and is generally useful beyond this example.

Design of the approval set:
- A gate carries `required_approvals: [{scope, quorum}]` (e.g. one `design:approve` per app +
  InfoSec, quorum = all).
- Resolution records each approver's identity + timestamp in the append-only audit log.
- The advancing trigger fires only when quorum is met across **distinct** identities.
- Rejection by any required party fails the gate and routes back to the prior stage.

## Automation map (agent-first, human-gated)

| Step | Automation | Gate |
| --- | --- | --- |
| Requirement handover | intake → impact analysis + kicks per-app architects → first-draft designs before a human touches it | architects review drafts |
| Consolidated design | integration-architect fixes integration patterns (payloads, channels, integration type) at design time | approval set: each app team + InfoSec |
| Build | harness dev agents implement against scoped repos; unit + regression on commit | code review (per team) |
| Defect | dev-agent produces first analysis + candidate fixes + clarifying query to QA | human dev reviews the analysis |
| QA / SIT | test-plan agent drafts cases from BRD + consolidated design; runs new + regression on build-ready; re-runs targeted test + regression on defect-fixed | QA signs off SIT |
| PT | pt-engineer runs perf on exposed services + cross-app regression | PT sign-off |
| Deploy | release-coordinator assembles the package | multi-party deploy sign-off; prod deploy scope is human-only |

## Knowledge bases (Curator-owned)

Requirements/BRD · per-app architecture · compliance (DORA/SAST/DAST/InfoSec) · **consolidated
design (integration patterns)** · per-app codebase · test-case + regression suite · defect log ·
decisions/ADR (audit trail) · env/infra inventory · runbook/support handover.

Refreshed **per requirement**: BRD, consolidated design, test cases, defects, decisions,
runbook. Mostly static (edited on change): app architecture, compliance policy, env inventory.

## Constraints

- Multi-party approval set is new (build first).
- Long-running durable saga across many human waits + the defect loop — a real test of
  checkpoint/resume.
- Real, distinct human approvers in the demo (not simulated).
- Build/deploy stop at the boundary; harness runs against demo repos, infra steps mocked.
- Deterministic (transitions, gate-counting) vs LLM (drafting, analysis, synthesis) split is
  explicit. MCP servers (Confluence/Jira/Git/CI/SAST/monitoring) are configured, never coded
  per-vendor — no hardcoded provider.

## Test plan

- **Approval set (unit):** quorum across distinct identities; duplicate approver rejected;
  any-party rejection fails the gate and routes back; audit records each approval.
- **Schema validation:** every topology/archetype/skill/trigger/workspace YAML validates
  against the canonical schemas.
- **Trigger graph (unit):** each state change fires the correct next stage; defect loop
  cycles back; advance only on quorum.
- **Pipeline dry-run (integration):** one requirement through intake → consolidated design →
  build (mock harness) → SIT (mock) → PT (mock) → deploy package, with mock MCP servers and
  scripted gate resolutions; asserts one audit trail, correct per-app scoping (an OMS agent
  denied a Web resource), and consistent KB state.

## Demo plan

`just demo-sdlc` (or a runnable script under `examples/sdlc-pipeline/`): submit a sample BRD,
watch intake produce impact analysis + per-app first-draft designs, resolve the multi-party
design gate as distinct identities, let the build/SIT/PT stages run against mocks, and produce
a deploy-ready package — with the full audit trail printed. Terminal transcript in the PR body.

## Build order (proposed slices)

1. Multi-party approval set (governance) + tests — the enabling primitive.
2. Archetypes + one-app (OMS) intake → design → approval, mock MCP — proves scoping + gates.
3. Consolidated design across all three apps (the cross-cutting synthesis).
4. Harness build + code-review gate against a demo repo.
5. SIT + PT (cross-app) with mock rigs; defect loop.
6. Deploy package + support handover; full `just demo-sdlc`.
