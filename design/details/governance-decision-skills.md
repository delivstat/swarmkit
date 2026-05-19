---
title: Governance decision skills — mandatory evaluation at workspace and topology level
description: Workspace-level baseline + topology-level override for mandatory decision skills. Trigger points, scope filtering, merge semantics, and built-in grounding skills.
tags: [governance, decision, grounding, skills]
status: proposed
---

# Governance decision skills — mandatory evaluation

## Goal

Let workspace authors declare decision skills that **must** run at
specific points during topology execution. Topologies inherit the
workspace baseline and can add or override. This enables grounding
constraints (citation checking, contradiction detection) without
baking policy into the compiler.

## Non-goals

- Rewriting the compiler to know about grounding. The compiler
  interprets topologies; the governance layer enforces policy.
- Making decision skills mandatory for all workspaces. Opt-in per
  workspace, with sensible defaults (none).
- Tier 2/3 judicial panels (already covered in `decision-skills.md`).
  This note covers **when** decision skills fire, not **how** they
  evaluate.

## Where this fits

```
design §8.5  GovernanceProvider interface
design §8.6  Tiered judicial model (Tier 1-3)
decision-skills.md  Decision skill anatomy + panels
THIS NOTE  When mandatory decision skills fire + merge semantics
```

The existing `decision-skills.md` covers Pattern 1 (inline) and
Pattern 2 (external judge). This note adds Pattern 3: **governance-
mandated evaluation** — decision skills the policy engine requires
regardless of what the topology author wired up.

## Schema

### Workspace level (baseline)

```yaml
# workspace.yaml
governance:
  provider: agt
  decision_skills:
    - id: grounding-verifier
      trigger: post_output
      required: true
    - id: contradiction-detector
      trigger: pre_synthesis
      required: true
```

### Topology level (override/extend)

```yaml
# topology YAML
governance:
  decision_skills:
    - id: citation-checker
      trigger: post_output
      scope: "jira-researcher,docs-researcher"
      required: true
    - id: grounding-verifier
      required: false   # disable workspace default for this topology
```

### Schema definition (`decision_skill_binding`)

```json
{
  "type": "object",
  "required": ["id", "trigger"],
  "additionalProperties": false,
  "properties": {
    "id": {
      "type": "string",
      "pattern": "^[a-z][a-z0-9-]*$",
      "description": "Decision skill ID. Must exist in workspace skill registry."
    },
    "trigger": {
      "enum": ["post_output", "checkpoint", "pre_synthesis"],
      "description": "When the skill fires during execution."
    },
    "scope": {
      "type": "string",
      "description": "Comma-separated agent IDs. Default '*' = all agents."
    },
    "required": {
      "type": "boolean",
      "default": true,
      "description": "If true, output is rejected when this skill is not satisfied."
    },
    "config": {
      "type": "object",
      "additionalProperties": true,
      "description": "Skill-specific configuration (thresholds, etc)."
    }
  }
}
```

## Trigger points

Three trigger points, each with clear semantics:

| Trigger | When it fires | What it receives | Use case |
|---------|--------------|------------------|----------|
| `post_output` | After any agent produces output, before it's returned to coordinator | Agent output text + agent ID + task instruction | Grounding verification, citation checking, fabrication detection |
| `checkpoint` | When coordinator reviews task plan status (between batches) | All completed task results + plan status | Cross-agent contradiction detection, coverage verification |
| `pre_synthesis` | Before coordinator's final synthesis, after all tasks complete | All task results + original input | Final quality gate, completeness check |

### Execution flow

```
Agent produces output
  → Tier 0-2: structural validation (existing)
  → post_output decision skills fire (NEW)
      → verdict=pass → output accepted
      → verdict=fail → output rejected, retry or escalate
  → Output returned to coordinator

Coordinator at checkpoint
  → checkpoint decision skills fire (NEW)
      → verdict=pass → continue
      → verdict=fail → coordinator gets feedback, can update plan

All tasks complete, before synthesis
  → pre_synthesis decision skills fire (NEW)
      → verdict=pass → coordinator synthesizes
      → verdict=fail → feedback injected into synthesis prompt
```

## Merge semantics

Topology inherits all workspace `decision_skills`. When a topology
declares the same `id`:

1. **Override:** topology's binding replaces workspace's. This allows
   `required: false` to disable a workspace default.
2. **Extend:** new IDs in topology are added to the merged set.
3. **Scope narrowing:** topology can add `scope` to restrict which
   agents a workspace-wide skill applies to.

Merge is by `id` — the topology entry wins for all fields.

```python
def merge_decision_skills(workspace, topology):
    merged = {s["id"]: s for s in workspace}
    for s in topology:
        merged[s["id"]] = s  # topology wins
    return [s for s in merged.values() if s.get("required", True)]
```

## Runtime enforcement

### GovernanceProvider additions

```python
class GovernanceProvider(ABC):
    # existing methods...

    @abstractmethod
    async def evaluate_decision_skill(
        self,
        *,
        skill_id: str,
        trigger: str,
        agent_id: str,
        content: str,
        context: dict[str, Any],
    ) -> DecisionSkillResult:
        """Evaluate a mandatory decision skill against agent output."""
        ...
```

```python
@dataclass(frozen=True)
class DecisionSkillResult:
    skill_id: str
    verdict: str         # pass | fail | needs-revision
    confidence: float    # 0.0-1.0
    reasoning: str
    flagged_items: list[str]  # specific issues found
```

### Compiler integration

The compiler doesn't know about grounding policy. Instead:

1. `WorkspaceRuntime` merges workspace + topology decision_skills
   at compile time.
2. The merged bindings are passed to the compiler as opaque config.
3. At each trigger point, the compiler calls
   `governance.evaluate_decision_skill()`.
4. GovernanceProvider dispatches to the appropriate decision skill
   implementation (llm_prompt, mcp_tool, composed).

The compiler's only responsibility: call governance at the right
trigger points and handle the verdict (accept, reject, or inject
feedback). It doesn't know what the decision skills check.

### Failure handling

| Verdict | Action |
|---------|--------|
| `pass` | Continue normally |
| `fail` + `post_output` | Inject flagged_items as feedback, agent retries (up to 2x) |
| `fail` + `checkpoint` | Inject flagged_items into coordinator's checkpoint review |
| `fail` + `pre_synthesis` | Inject flagged_items into synthesis prompt as "known issues" |
| `needs-revision` | Same as `fail` but softer — suggestions, not rejections |

After max retries exhausted: submit to review queue for human review
(same escalation path as existing decision skills).

## Built-in grounding skills

SwarmKit ships three reference grounding skills. Workspace authors
opt in by adding them to `governance.decision_skills`.

### `grounding-verifier`

**Trigger:** `post_output`

Checks agent output for:
- Claims without source attribution
- Fabricated names, codes, or identifiers
- Expanded acronyms not verified from source
- Specific data (timestamps, IDs, counts) not traceable to tool output

**Verdict:** `pass` if all claims are sourced, `fail` with list of
unsourced claims.

### `contradiction-detector`

**Trigger:** `pre_synthesis`

Compares all completed task results for:
- Contradictory facts (agent A says X, agent B says not-X)
- Inconsistent numbers or dates across sources
- Conflicting status information

**Verdict:** `pass` if no contradictions, `fail` with list of
contradictions and which agents disagree.

### `citation-checker`

**Trigger:** `post_output`

Stricter than grounding-verifier. Requires every factual claim to
have an explicit citation (tool name, file path, ticket ID, etc).
Best for research-heavy topologies.

**Verdict:** `pass` if all claims cited, `fail` with uncited claims.

## Implementation plan

### PR 1: Design note (this PR)

### PR 2: Schema changes
- Add `decision_skills` to workspace governance block
- Add `governance` block to topology schema (new — currently absent)
- Add `decision_skill_binding` definition to both schemas
- Update Python + TS codegen
- Tests: validation of both schemas with decision_skills

### PR 3: Merge logic + GovernanceProvider extension
- `merge_decision_skills()` in workspace runtime
- `evaluate_decision_skill()` on GovernanceProvider ABC
- Mock implementation for tests
- `DecisionSkillResult` dataclass
- Tests: merge semantics (override, extend, disable)

### PR 4: Compiler trigger points
- `post_output` hook after agent output, before return
- `checkpoint` hook in task plan checkpoint review
- `pre_synthesis` hook before coordinator synthesis
- Verdict handling (retry, feedback injection, escalation)
- Tests: each trigger point fires, verdict handling works

### PR 5: Built-in grounding skills
- `grounding-verifier` skill YAML + llm_prompt implementation
- `contradiction-detector` skill YAML + llm_prompt implementation
- `citation-checker` skill YAML + llm_prompt implementation
- Tests: each skill against known-good and known-bad outputs

### PR 6: Sterling workspace integration
- Wire grounding-verifier + contradiction-detector into Sterling
  workspace governance
- Wire citation-checker for jira-researcher + docs-researcher
- Add grounding prompt guidance to Sterling archetypes
- E2E test: run Sterling with grounding enabled

## Test plan

- **Workspace-only binding:** workspace declares grounding-verifier,
  topology inherits it. Verify it fires post_output for all agents.
- **Topology override:** topology disables grounding-verifier.
  Verify it does NOT fire.
- **Topology extend:** topology adds citation-checker scoped to
  one agent. Verify it fires only for that agent.
- **Merge semantics:** workspace + topology both declare same skill.
  Verify topology wins.
- **Verdict handling:** mock decision skill returns fail → verify
  agent retries with feedback.
- **Escalation:** mock decision skill fails after retries → verify
  item appears in review queue.
- **E2E:** Sterling topology with grounding → verify fabricated
  output is caught and flagged.
