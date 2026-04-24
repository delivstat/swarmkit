---
title: Decision skills — LLM judges, panels, and escalation
description: Tier 2-3 judicial evaluation. How verdicts, confidence scores, and reasoning flow through the runtime. Multi-persona panels for high-stakes decisions.
tags: [skills, decision, judicial, m4]
status: proposed
---

# Decision skills — LLM judges + multi-persona panels

## Goal

Decision skills evaluate agent output against a rubric and return a
structured verdict. They are the judicial pillar's runtime mechanism
(design §8.6 Tiers 2-3). This note covers how they work, how they
compose into panels, and how they interact with the output governance
tiers (0-2) defined in `structured-output-governance.md`.

## Where decision skills sit

```
Agent produces output
  → Tier 0: structured generation (provider constrains shape)
  → Tier 1: JSON Schema validation (deterministic)
  → Tier 2: business rules (deterministic)
  → Tier 3: decision skill evaluation (this note)
      ├── Single judge (Tier 2 in §8.6 terms)
      └── Multi-persona panel (Tier 3 in §8.6 terms)
```

Tiers 0-2 catch structural errors at near-zero cost. Tier 3 catches
semantic errors — "the reasoning is wrong", "this misses the security
implication", "the confidence doesn't match the evidence". These
require an LLM.

## Decision skill anatomy

A decision skill is a regular skill with `category: decision` and a
structured `outputs` block:

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: code-quality-review
  name: Code Quality Review
  description: Evaluates code against quality standards.
category: decision
outputs:
  type: object
  properties:
    verdict:
      type: string
      enum: [pass, fail]
    confidence:
      type: number
      minimum: 0
      maximum: 1
    reasoning:
      type: string
      minLength: 20
  required: [verdict, confidence, reasoning]
implementation:
  type: llm_prompt
  prompt: |
    Evaluate the following code against these quality criteria:
    - Readability: clear names, consistent style
    - Correctness: handles edge cases, no obvious bugs
    - Maintainability: small functions, low coupling

    Return your verdict as JSON with verdict, confidence, and reasoning.
provenance:
  authored_by: human
  version: 1.0.0
```

The `outputs` block is standard JSON Schema (per the M4.1 change).
The auto-correction loop (M4.2) enforces it — if the judge returns
`confidence: 1.5`, the validator catches it and re-prompts.

## How decision skills are invoked

Two invocation patterns:

### Pattern 1: inline evaluation (agent evaluates its own output)

The agent's archetype includes a decision skill. After the agent
produces output, the compiler's output governance checks it:

1. Tiers 0-2 validate structure
2. If the agent has a decision skill → invoke it on the output
3. Decision skill returns verdict + confidence + reasoning
4. If `verdict=fail` or `confidence < threshold` → escalate

This is the simple case — the agent self-evaluates.

### Pattern 2: external judge (separate agent evaluates another's output)

A topology declares a judge agent whose role is evaluation:

```yaml
agents:
  root:
    role: root
    children:
      - id: writer
        role: worker
        archetype: content-writer
      - id: judge
        role: worker
        archetype: content-judge
```

The root delegates to the writer, then delegates the writer's output
to the judge. The judge returns a verdict. If `fail`, the root can
re-delegate to the writer with the judge's reasoning as feedback.

This is the more common pattern — separation of concerns between
producer and evaluator. The root (leader) orchestrates the
write → judge → revise cycle.

## Confidence thresholds and escalation

Configurable per topology in `runtime_config`:

```yaml
runtime_config:
  evaluation:
    confidence_threshold: 0.7
    on_low_confidence: escalate_to_panel
    on_fail: retry_with_feedback
    max_retries: 2
```

| Outcome | Action |
|---|---|
| `verdict=pass`, `confidence >= threshold` | Accept output |
| `verdict=pass`, `confidence < threshold` | Escalate to panel or HITL |
| `verdict=fail` | Retry with judge's reasoning as feedback, up to max_retries |
| Retries exhausted | Submit to review queue for human review |

## Multi-persona panels (Tier 3)

For high-stakes decisions, multiple judges evaluate in parallel with
different perspectives:

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: security-review-panel
  name: Security Review Panel
  description: Three-judge panel for security-sensitive code.
category: decision
implementation:
  type: composed
  composes:
    - security-vulnerability-scan
    - dependency-risk-check
    - access-control-review
  strategy: parallel-consensus
outputs:
  type: object
  properties:
    verdict:
      type: string
      enum: [pass, fail]
    confidence:
      type: number
      minimum: 0
      maximum: 1
    reasoning:
      type: string
    panel_votes:
      type: array
      items:
        type: object
        properties:
          judge:
            type: string
          verdict:
            type: string
          confidence:
            type: number
        required: [judge, verdict, confidence]
  required: [verdict, confidence, reasoning, panel_votes]
```

### Consensus strategies

| Strategy | How it works |
|---|---|
| `parallel-consensus` | All judges run in parallel. Final verdict = majority. Confidence = average of agreeing judges. |
| `sequential` | Judges run in order. First `fail` stops the chain. |
| `custom` | User-defined aggregation logic (future). |

### Panel implementation

The runtime handles composed decision skills:

1. Fan out to all constituent skills (parallel or sequential)
2. Collect individual verdicts
3. Aggregate per strategy:
   - `parallel-consensus`: majority vote, average confidence
   - `sequential`: first-fail short-circuit
4. Record all individual votes in `panel_votes`
5. Return the aggregated verdict

## Review queue primitive

When a decision escalates to HITL, it goes to the review queue:

```python
@dataclass(frozen=True)
class ReviewItem:
    id: str
    topology_id: str
    agent_id: str
    skill_id: str
    output: dict[str, Any]
    verdict: dict[str, Any]  # the judge's verdict
    reason: str              # why it escalated
    timestamp: datetime
    status: Literal["pending", "approved", "rejected"]
```

v1.0 implementation: file-backed JSON under `.swarmkit/reviews/`.
Each item is a JSON file. `swarmkit review list` shows pending items.
`swarmkit review approve/reject <id>` resolves them.

Pluggable storage (database, external system) is a follow-up — the
interface is a simple `ReviewQueue` protocol with `submit`, `list`,
`resolve` methods.

## Skill gap log

When decisions fail repeatedly or confidence stays low, the runtime
records a **skill gap** — a pattern of capability shortfall:

```python
@dataclass(frozen=True)
class SkillGap:
    skill_id: str
    topology_id: str
    pattern: str           # e.g. "confidence < 0.5 on 3 consecutive runs"
    suggested_action: str  # e.g. "consider adding a specialized judge"
    first_seen: datetime
    occurrences: int
```

Skill gaps are the input to the swarm growth cycle (design §12) —
they surface areas where the swarm needs new or improved skills.

v1.0: appended to `.swarmkit/gaps.jsonl`. `swarmkit gaps list` shows
them. The authoring AI reads them when suggesting new skills.

## Implementation plan

### PR 1 (this PR): design note

### PR 2: review queue + skill gap log primitives

- `ReviewQueue` protocol + file-backed implementation
- `SkillGapLog` protocol + JSONL implementation
- CLI: `swarmkit review list/approve/reject`, `swarmkit gaps list`
- Tests: submit, list, resolve review items; record + query skill gaps

### PR 3: decision skill runtime wiring

- Wire `llm_prompt` implementation type in the compiler (currently
  only `mcp_tool` and `composed` exist)
- Invoke decision skills on agent output when configured
- Record evaluation events via GovernanceProvider
- Tests: mock judge → verdict → acceptance/retry/escalation

### PR 4: multi-persona panel aggregation

- Implement `parallel-consensus` and `sequential` strategies
- Fan-out/fan-in for composed decision skills
- Tests: majority vote, first-fail, panel_votes recorded

## Test plan

- **Single judge accepts.** Judge returns `pass` with high confidence
  → output accepted, no escalation.
- **Single judge rejects.** Judge returns `fail` → retry with feedback
  → second attempt passes.
- **Low confidence escalates.** Judge returns `pass` but confidence
  below threshold → escalation to panel or HITL.
- **Panel majority.** Three judges: two pass, one fails → verdict=pass.
- **Panel unanimous fail.** All three fail → verdict=fail, retry.
- **Review queue.** Escalated item appears in queue, can be approved/
  rejected via CLI.
- **Skill gap logged.** Three consecutive low-confidence runs →
  gap entry recorded with pattern description.

## Exit demo

Extend the hello-world topology: worker output goes through a Tier 2
LLM judge. Low-confidence verdict lands in the review queue. A
failing run appears in the skill gap log.
