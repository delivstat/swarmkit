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

## Human-in-the-loop — how the human is notified and closes the loop

Three layers, shipped at different milestones:

### Layer 1: inline HITL in `swarmkit run` (M4, task #48)

For one-shot execution, the human is at the terminal. When a review
is needed, the CLI **pauses and asks inline**:

```
⏸ Review required: code-quality-review returned low confidence (0.3)
  Output: {"verdict": "pass", "confidence": 0.3, "reasoning": "..."}

  [a]pprove  [r]eject  [s]how details
> a
✓ Approved. Continuing execution.
```

No async notification needed — the operator is watching. The
execution resumes immediately on approval. On rejection, the
rejection reason is fed back to the agent as feedback for retry.

This is the primary HITL experience for `swarmkit run`.

### Layer 2: `swarmkit review` CLI (M4, task #49)

For reviewing items after execution or for batch review:

```bash
swarmkit review list
#   ID        Agent      Skill              Reason
#   a1b2c3    worker-1   code-quality       confidence 0.3
#   d4e5f6    worker-2   security-scan      retries exhausted

swarmkit review show a1b2c3     # full output + verdict + reasoning
swarmkit review approve a1b2c3  # resolved
swarmkit review reject a1b2c3   # resolved with feedback
```

Works for any review items — from `swarmkit run`, `swarmkit serve`,
or items submitted programmatically.

### Layer 3: notification plugins for `swarmkit serve` (M9, task #50)

For long-running swarms, the human isn't at the terminal. Configured
in `workspace.yaml`:

```yaml
notifications:
  - type: slack
    channel: "#swarm-reviews"
    on: [review.pending]
  - type: email
    to: admin@company.com
    on: [review.pending, gap.detected]
  - type: webhook
    url: https://internal.company.com/swarm-events
    on: [review.pending]
```

The notification plugin shape is a `Protocol` with a `notify(event)`
method. Built-in plugins: Slack, email, webhook. Custom plugins via
entry points.

The human gets notified, reviews in the v1.1 UI or via `swarmkit
review` CLI, and the review queue's `resolve()` method closes the
loop regardless of how the human was notified.

### Process death and recovery

**`swarmkit run` (one-shot):** process dies mid-execution or while
waiting for HITL → session is lost. Same as closing any terminal
command. No recovery needed — the user re-runs.

**`swarmkit serve` (persistent — M9):** process death must be
recoverable. Three mechanisms:

1. **Review items persist to disk.** `FileReviewQueue` writes JSON
   files immediately. If the process dies after submitting a review
   item, the item survives and can be resolved after restart.

2. **Execution state is checkpointed.** LangGraph's `SqliteSaver`
   checkpoints every graph step to `.swarmkit/state/<topology>.db`.
   On restart, execution resumes from the last checkpoint.

3. **HITL is non-blocking in serve mode.** Unlike `swarmkit run`
   (which blocks the terminal), `swarmkit serve` uses
   **checkpoint-based HITL**: submit the review item → checkpoint
   graph state as `"paused:review:<item-id>"` → release the
   execution slot. When the review is resolved (via CLI, webhook, or
   UI), the runtime loads the checkpoint and resumes execution with
   the review decision. The process can die and restart at any point
   in this flow.

```
Execution → HITL gate → submit review (persisted)
                       → checkpoint state as "paused:review:abc123"
                       → process can safely die here

Process restarts (or never died)
  → scan reviews/ for resolved items
  → find matching checkpoint
  → resume execution with the decision
```

This is M9 scope — the `SqliteSaver` checkpointer and non-blocking
HITL are wired alongside `swarmkit serve`.

### Layer 4: UI review dashboard (v1.1)

Web interface for reviewing + resolving items. Out of scope for v1.0
— CLI + notifications cover the launch.

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

### PR 2: review queue + skill gap log primitives (done — PR #41)

- `ReviewQueue` protocol + file-backed implementation ✓
- `SkillGapLog` protocol + JSONL implementation ✓
- Tests: submit, list, resolve review items; record + query skill gaps ✓

### PR 3: inline HITL + review CLI (task #48, #49)

- Inline HITL in compiler: pause `swarmkit run` when review needed,
  prompt human in terminal, resume on approve/reject.
- `swarmkit review list/show/approve/reject` CLI commands.
- `swarmkit gaps list` CLI command.

### PR 4: decision skill runtime wiring

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
