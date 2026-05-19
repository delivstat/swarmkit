---
title: Scope freeze + spec-conformance — targeted synthesis quality for ticket-driven design
description: Two-step mechanism to prevent plausibility-from-priors errors in solution design. Phase 1 freezes scope from the ticket; pre_synthesis decision skill validates output against frozen scope.
tags: [governance, decision, grounding, quality, sterling]
status: proposed
---

# Scope freeze + spec-conformance skill

## Problem

When a model produces a solution design from a Jira ticket, it
interpolates from training priors rather than strictly deriving from
the spec. This produces output that reads as competent but contains:

1. **Fabrication-of-relationship** — real entity IDs connected by
   invented relationships (PROJ-101 "related to" PROJ-100)
2. **Wrong source selection** — keyword-dense pages surfaced over
   canonically correct pages (BORIS overview vs Returns V2 spec)
3. **Plausibility-from-priors** — model picks the "typical" answer
   over the spec-specific answer (refund-on-Approved vs refund-on-
   Item-Picked)

Generic grounding catches #1 (provenance check). Nothing currently
catches #2 and #3 because both involve real data applied incorrectly.

## Solution: two mechanisms

### 1. Scope freeze (architect-side)

After the architect reads the Jira ticket (Phase 1 of two-phase
planning), it produces a **scope document** that constrains all
downstream work. The scope is not a summary — it's a contract.

### 2. Spec-conformance skill (governance decision skill)

A `pre_synthesis` decision skill that takes the frozen scope +
synthesis output and checks: does the output satisfy the ACs? Does
it contradict the spec? Does it introduce claims outside scope?

## Scope freeze: what it contains

```yaml
# Written by architect to .swarmkit/run-state/current/scope.json
{
  "ticket_id": "PROJ-100",
  "acceptance_criteria": [
    "OMS must support return process for replacement orders",
    "Send Return Initiated feed to SAP with PaymentDetailsList",
    "SAP must accept return feed with replacement for same return",
    "Initiate refund and send refund feed to SAP",
    "Return Invoice sent per existing triggers"
  ],
  "linked_tickets": ["PROJ-99"],
  "parent_epic": "EPICS-9865",
  "stakeholder_inputs": [
    {
      "author": "gopu",
      "date": "2026-04-29",
      "key_point": "RTO sub-scenario must be handled separately"
    }
  ],
  "authoritative_sources": [
    "Confluence page 3820945422 (OMS Implementation Overview)",
    "Returns V2 specification pages"
  ],
  "excluded_from_scope": [
    "Non-replacement order returns (existing flow, no changes)",
    "Marketplace/Web channel changes (not in Jira scope)"
  ],
  "key_constraints": [
    "Refund triggers on Item Picked (3700.104), NOT on Approved",
    "Must handle multi-level hierarchy / services / free-product / bundles"
  ]
}
```

## Scope freeze: how it's produced

The compiler auto-injects `freeze-scope` as a platform tool for
any agent with task planning tools. The architect calls it after
reading source material:

```
freeze-scope({
  source: "PROJ-100",
  requirements: ["OMS must support return for replacement orders", ...],
  constraints: ["Refund triggers on Item Picked (3700.104)"],
  authoritative_sources: ["Returns V2 spec pages"],
  excluded: ["Non-replacement returns", "Marketplace channel"],
  decisions: [{"by": "gopu", "date": "2026-04-29", "decision": "RTO sub-scenario"}],
  related: ["PROJ-99"]
})
```

The tool validates the schema and writes `scope.json` to disk.
All subsequent task instructions MUST reference items from this
scope. The spec-conformance skill validates against it.

## Scope freeze: where it lives

- **Runtime path:** `.swarmkit/run-state/current/scope.json`
- **Written by:** `freeze-scope` platform tool (called by architect)
- **Read by:** spec-conformance decision skill (pre_synthesis)
- **Injected:** auto-injected alongside task planning tools for
  any coordinator with 2+ children
- **Passed to workers:** key items embedded in task instructions
  (workers don't read scope.json directly — the architect
  translates scope into specific instructions per worker)

## Spec-conformance skill

### Skill definition

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: spec-conformance
  name: Spec Conformance Checker
  description: >
    Validates solution design output against frozen scope from the
    Jira ticket. Checks AC satisfaction, contradiction with spec,
    and out-of-scope claims. Requires scope.json in run state.
category: decision
outputs:
  type: object
  properties:
    verdict:
      type: string
      enum: [pass, fail, needs-revision]
    confidence:
      type: number
      minimum: 0
      maximum: 1
    reasoning:
      type: string
    ac_coverage:
      type: array
      items:
        type: object
        properties:
          ac:
            type: string
          satisfied:
            type: boolean
          evidence:
            type: string
          issue:
            type: string
        required: [ac, satisfied]
    contradictions:
      type: array
      items:
        type: object
        properties:
          claim:
            type: string
          spec_says:
            type: string
          severity:
            type: string
            enum: [critical, major, minor]
        required: [claim, spec_says, severity]
    out_of_scope:
      type: array
      items:
        type: object
        properties:
          claim:
            type: string
          reason:
            type: string
        required: [claim, reason]
  required: [verdict, confidence, reasoning, ac_coverage]
implementation:
  type: llm_prompt
  prompt: |
    You are a spec-conformance reviewer for OMS solution designs.
    You receive a frozen scope (from the Jira ticket) and a solution
    design output. Your job is to check three things:

    1. AC COVERAGE: Does the design address every acceptance
       criterion in the scope? For each AC, state whether it's
       satisfied and cite the evidence from the design. If an AC
       is not addressed, flag it.

    2. CONTRADICTIONS: Does the design contradict any constraint
       in the scope? Check specific status codes, trigger points,
       linked tickets, and stakeholder decisions. If the design
       says X but the spec says Y, flag the contradiction with
       severity.

    3. OUT-OF-SCOPE CLAIMS: Does the design introduce entities,
       relationships, or scope that the ticket doesn't cover?
       Check: are channels listed that aren't in the Jira scope?
       Are tickets listed as "related" that aren't in the linked
       tickets? Are scenarios covered that weren't requested?

    IMPORTANT: You are not checking whether the design is GOOD.
    You are checking whether it MATCHES THE SPEC. A mediocre
    design that satisfies all ACs passes. A brilliant design that
    misses an AC or contradicts a constraint fails.

    Verdict:
    - pass: all ACs satisfied, no critical/major contradictions,
      no significant out-of-scope claims
    - needs-revision: minor gaps or contradictions that can be
      fixed without re-research
    - fail: missing ACs, critical contradictions, or major scope
      violations that require re-research
provenance:
  authored_by: human
  version: 1.0.0
```

### How it fires

```yaml
# workspace.yaml or topology.yaml
governance:
  decision_skills:
    - id: spec-conformance
      trigger: pre_synthesis
      scope: "sterling-oms-architect"
      config:
        scope_file: ".swarmkit/run-state/current/scope.json"
```

### What the evaluator does

When `spec-conformance` fires at `pre_synthesis`:

1. Read `scope.json` from the run state directory
2. Build input: scope document + all completed task results +
   synthesis output
3. Invoke the LLM with the skill prompt
4. Parse the structured result
5. If verdict=fail → inject feedback into synthesis prompt:
   "These ACs are not addressed: [...]. These claims contradict
   the spec: [...]. Fix before finalising."

### Retry behavior

Uses the existing governance retry loop (max 4 attempts):
- Attempt 1: architect produces synthesis
- pre_synthesis fires → spec-conformance fails (AC-3 not addressed)
- Feedback injected: "AC-3 not addressed: 'Initiate refund and
  send refund feed to SAP' — your design doesn't specify the
  refund trigger point"
- Attempt 2: architect revises synthesis with the feedback
- pre_synthesis fires again → pass

## What this catches vs what it doesn't

| Error class | Caught by | Mechanism |
|---|---|---|
| Fabricated entity IDs | grounding-verifier (post_output) | "Is this ID in tool results?" |
| Fabricated relationships | grounding-verifier (post_output) | "Did any tool show this link?" |
| Wrong source selection | scope freeze + spec-conformance | "Is this page in authoritative_sources?" |
| Plausibility-from-priors | spec-conformance (pre_synthesis) | "Spec says X, design says Y" |
| Missing AC coverage | spec-conformance (pre_synthesis) | AC checklist comparison |
| Out-of-scope inflation | spec-conformance (pre_synthesis) | "Is this in excluded_from_scope?" |
| Subtle domain errors | Sterling-specific judge (future) | Domain knowledge required |

The last row — subtle domain errors where even the spec doesn't
explicitly state the rule — remains uncatchable by automated means.
This is where human review earns its keep. The goal isn't zero
defects; it's catching the 80% that are structurally detectable.

## Implementation plan

### PR 1: Scope freeze in architect workflow
- Architect self-task at Phase 1 checkpoint writes `scope.json`
- Task instruction template for scope extraction
- `_task_executor.py`: pass `workspace_root` context so self-tasks
  can write to run-state
- Test: verify scope.json written after jira-researcher completes

### PR 2: spec-conformance skill
- Skill YAML in `reference/skills/` and Sterling workspace
- Governance binding: `pre_synthesis` trigger, scoped to architect
- `_decision_evaluator.py`: read scope.json, include in skill input
- Test: mock skill returns fail for missing AC → retry injects feedback

### PR 3: Enhanced grounding-verifier for relationship claims
- Update grounding-verifier prompt to check relationship claims
  ("X is related to Y" — was this relationship in tool results?)
- Test: fabricated relationship flagged

### PR 4: Sterling workspace integration
- Wire spec-conformance + updated grounding-verifier
- Update architect archetype to produce scope.json
- E2E test: PROJ-100 re-run with scope freeze

## Open questions

1. **Scope.json format:** JSON shown above, or YAML for consistency
   with other SwarmKit artifacts? JSON is simpler for LLM parsing.

2. **Who produces scope.json — architect or a dedicated skill?**
   Currently: architect self-task. Alternative: a `scope-extractor`
   skill that the governance layer auto-invokes. The architect
   approach is simpler and doesn't require new runtime machinery.

3. **What if the ticket doesn't have clear ACs?** The scope
   extraction must still produce something — even if it's "no
   explicit ACs found, deriving from description: [...]". The
   spec-conformance skill can't work with an empty scope.

4. **Cost:** spec-conformance fires once per synthesis (not per
   agent). With retry, worst case = 4 LLM calls. At the
   pre_synthesis point, this is acceptable — the research phase
   (50+ tool calls across workers) already cost far more.

## Relationship to existing mechanisms

- **Two-phase planning (PR #208):** produces the raw material
  (architect reads ticket first). Scope freeze structures it.
- **Governance decision skills (v1.2.13):** provides the runtime
  infrastructure. Spec-conformance is just another decision skill.
- **Retry loop (v1.2.13):** provides the correction mechanism.
  Failed spec-conformance → feedback → architect revises.
- **Grounding-verifier:** catches fabrication. Spec-conformance
  catches semantic errors. Different failure classes, same
  governance infrastructure.
