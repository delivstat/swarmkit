# 17 — Phase 7: Growth / self-improvement loop

Builds on the gap log + eval ([06](06-observability-eval.md), [08](08-cli.md)), review
([05](05-identity-governance-iam.md)), and the registry ([15](15-artifact-registry.md)). Designs the
fleet-scale version of SwarmKit's third pillar — **swarms grow through human-approved authoring** —
closing the loop the control plane was built to enable.

## Goal

Turn signals (skill gaps, eval regressions, drift) into proposed improvements, tested and
**human-approved** before they deploy — across the fleet, with every gate human-held.

## The loop

```
 signal ──▶ surface ──▶ propose ──▶ test ──▶ approve ──▶ publish ──▶ deploy
 (gap/      (panel)    (authoring  (eval    (human,     (registry   (governed
  eval/                 swarm       harness)  reserved    version)    push)
  drift)                drafts)               scope)
```

1. **Signal** — cross-instance **skill-gap aggregation** ([14](14-aggregation.md)) from per-instance
   gap logs; **eval regressions**; **intent drift** clusters.
2. **Surface** — the panel ranks gaps (frequency × impact) and presents them.
3. **Propose** — the **authoring swarm** drafts a skill/topology change (conversational authoring,
   [16](16-fleet-ui.md)). It holds only `skills:write_pending` ([05](05-identity-governance-iam.md)) —
   it can draft, never activate.
4. **Test** — the **eval harness** ([06](06-observability-eval.md)) runs the proposal against an
   eval-set; regressions block.
5. **Approve** — a **human** reviews in the approvals queue. Activation/deploy require
   reserved-for-human scopes (`skills:activate`, `topologies:modify`) — structurally un-grantable to
   the panel or the authoring swarm.
6. **Publish** — approved artifact → a new **registry version** ([15](15-artifact-registry.md)) with
   provenance.
7. **Deploy** — **governed push** to target instances (canary for topologies), audited.

## Separation of powers (non-negotiable)

Every transition that changes rules or activates capability is **human-gated**
([05](05-identity-governance-iam.md), §8.7). The loop is **proposal automation**, not autonomous
self-modification: nothing auto-activates or auto-deploys. The authoring swarm is executive; humans
are legislative/judicial; the audit log (media) records every step.

## Self-improvement specifics

- **Eval-driven:** a regression on an instance can auto-open a proposal (re-author + re-test), but
  the fix still needs human approval.
- **Gap-driven:** repeated `skill_gap_surfaced` ([06](06-observability-eval.md)) for the same
  capability ranks up and triggers a proposal.
- **Fleet learning:** an improvement approved once can be offered for deploy to other instances
  exhibiting the same gap (with per-instance approval).

## Ties

Gap log + notifications ([06](06-observability-eval.md)) → aggregation ([14](14-aggregation.md)) →
authoring ([16](16-fleet-ui.md)) → eval ([06](06-observability-eval.md)) → review
([05](05-identity-governance-iam.md)) → registry + deploy ([15](15-artifact-registry.md)).

## What Phase 7 builds

Cross-instance gap aggregation + ranking; the proposal pipeline (signal → authoring-swarm draft →
eval → approval queue item); wiring approved proposals into the registry + governed deploy;
eval-regression-triggered proposals. Depends on Phases 3–6.

## Open questions / risks

- **Autonomy bounds** — keep proposal-only; never auto-activate. Make this a hard, tested invariant.
- Ranking heuristic for gaps (frequency × impact × cost) — needs the cost signal (blocked on
  `cost_usd`, [14](14-aggregation.md)).
- Avoiding proposal spam — dedup/cooldown on repeated gaps.
