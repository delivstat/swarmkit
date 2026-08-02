# Validating a topology's output

Where to put a validation layer so a run cannot return a confidently wrong answer, and what the
runtime actually does when validation fails.

Verified against runtime 1.131.0.

## Two layers, and they are not interchangeable

| | `output_schema` | decision skill |
|---|---|---|
| Checks | **shape** — fields, types, enums, required | **semantics** — is it true, grounded, in scope |
| Cost | free, deterministic | a tool call, or an LLM call per evaluation |
| On failure | structured generation retries the model | feeds the agent revision instructions |

Use both. Spending a judge call to discover a `summary` field is missing is the expensive way to
learn a cheap fact, and the schema cannot tell you the summary is fabricated.

## Layer 1: `output_schema` (shape)

Not a skill — a field on the agent, enforced before any judge runs:

```yaml
# topologies/research.yaml
root:
  id: coordinator
  archetype: coordinator
  output_schema:
    type: object
    required: [summary, findings, confidence]
    properties:
      summary: { type: string }
      confidence: { type: number, minimum: 0, maximum: 1 }
      findings:
        type: array
        items:
          type: object
          required: [claim, evidence]
          properties:
            claim: { type: string }
            evidence: { type: string }
```

Set `output_schema: null` on an agent to opt out of an archetype's default.

This eliminates shape-level hallucination outright. It does not, and cannot, tell you whether
`evidence` supports `claim`.

## Layer 2: the decision skill (semantics)

A decision skill is a **skill**, not compiler behaviour — grounding and conformance logic belongs
in the artifact, never in the runtime. The example below is an `llm_prompt`; see
[A decision skill does not have to be a prompt](#a-decision-skill-does-not-have-to-be-a-prompt)
for the deterministic, MCP-backed form, which is the better choice whenever the check is
decidable.

### The binding

```yaml
# topologies/research.yaml
governance:
  decision_skills:
    - id: output-conformance
      trigger: post_output
      scope: coordinator          # comma-separated agent ids; default '*' = every agent
      required: true
      config:
        max_retries: 2
```

`scope` matters more than it looks. The default `*` fires the skill after **every agent's**
output, including each sub-agent — that is N judge calls per run, not one. Name the root agent
when you mean "the topology's answer".

### The skill

```yaml
# skills/output-conformance.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: output-conformance
  name: Output Conformance Checker
  description: Checks the final answer against the request and its own evidence.
category: decision
outputs:
  type: object
  required: [verdict, confidence, reasoning]
  properties:
    verdict: { type: string, enum: [pass, fail, needs-revision] }
    confidence: { type: number, minimum: 0, maximum: 1 }
    reasoning: { type: string }
    violations:
      type: array
      items:
        type: object
        required: [claim, issue]
        properties:
          claim: { type: string }
          issue: { type: string }
implementation:
  type: llm_prompt
  prompt: |
    You are checking a finished answer before it is returned.

    1. GROUNDING: is every claim supported by the evidence cited alongside it?
    2. SCOPE: does it answer what was asked, without inventing adjacent scope?
    3. CONTRADICTION: does any part contradict another part?

    You are not judging whether the answer is GOOD. A mediocre answer that is
    grounded and in scope passes. A brilliant one with an unsupported claim
    does not.

    verdict:
      pass           - grounded, in scope, self-consistent
      needs-revision - fixable without redoing the work
      fail           - unsupported claims or material scope violations

    Write `reasoning` and `violations` as INSTRUCTIONS TO THE AGENT THAT WILL
    FIX THIS, naming the specific claim. That text becomes the retry prompt.
provenance:
  authored_by: human
  version: 1.0.0
```

`verdict` must be one of `pass`, `fail` or `needs-revision` (casing and `_`/`-` are normalised).
An unrecognised value is **read as `pass`**, not rejected — so a typo disables the check rather
than breaking it. See
[the one contract a tool must meet](#the-one-contract-a-tool-must-meet).

## A decision skill does not have to be a prompt

Nothing about the *binding* says LLM. A decision skill takes any of the three skill implementation
types, and the evaluator dispatches through the same executor every other skill uses:

```yaml
# skills/schema-conformance.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: schema-conformance
category: decision
outputs:
  type: object
  required: [verdict, confidence, reasoning]
  properties:
    verdict: { type: string, enum: [pass, fail, needs-revision] }
    confidence: { type: number, minimum: 0, maximum: 1 }
    reasoning: { type: string }
implementation:
  type: mcp_tool
  server: rynko-flow
  tool: validate_order_validation
  arguments:
    strict: true
```

The binding is unchanged — it does not know or care how the skill is implemented. That is the
point of the seam.

**Prefer this wherever the question has a computable answer.** A JSON-schema check, a lint run, a
test suite, a real validator: those are decidable, and asking a model to judge them adds cost,
latency and a failure mode for nothing. `reference/skills/` already ships several of this shape —
`lint-check`, `run-tests`, `security-scan`, `validate-workspace`, `gate-validator`.

Keep `llm_prompt` for the genuinely fuzzy part: *is this claim grounded in the evidence beside it,
is this in scope*. A `composed` skill with `strategy: parallel-consensus` runs both and requires
them to agree.

### The one contract a tool must meet

The tool has to return JSON carrying `verdict`, spelled exactly `pass`, `fail` or
`needs-revision`.

The parser is forgiving about packaging — it strips markdown fences, digs a `{...}` out of
surrounding prose, and drops trailing `[source: ...]` provenance tags that MCP servers append. It
is **not** forgiving about vocabulary:

!!! danger "An unrecognised verdict is read as `pass`"

    A validator returning `{"valid": false}` or `{"status": "rejected"}` does not fail the check —
    it passes it, because the parser defaults an unrecognised verdict to `pass`. A validation
    layer that reports success on every rejection is worse than no validation layer.

    Since 1.131.0 both cases log a warning naming the skill and the value
    (`the check is not running`), so this is visible rather than silent. It still passes — failing
    closed would turn every currently-passing run whose skill emits an odd verdict into a flagged
    one, which is its own outage — so treat the warning as the signal.

    Map your tool's vocabulary to the verdict enum, either inside the tool or with a thin
    `composed` wrapper around it. Then prove it with a run that should fail.

**Form is forgiven; vocabulary is not.** `FAIL`, `Fail`, ` fail ` and `needs_revision` all read
correctly — casing and separators are not part of the meaning, and models vary both constantly.
(Before 1.131.0 they did not, so a skill that plainly said `FAIL` was recorded as a pass.) But
`rejected`, `invalid` and `false` are still unrecognised: guessing at a synonym would invent a
verdict the skill never gave.

Two smaller notes:

- The evaluator reads `flagged_items`, and also `uncited_claims` and `contradictions`, whether the
  entries are strings or objects carrying `claim` / `description`. Existing validator output often
  already fits one of those.
- MCP-backed decision skills need `mcp_manager` wired. `swarmkit serve` and the CLI runtime do
  this. If you evaluate skills through a custom `GovernanceProvider`, pass it through or the tool
  call has nothing to execute against.

## What `fail` actually does

This is the part worth knowing **before** you write the prompt.

On `fail`, the runtime does not reject the run. It builds feedback from the failed results and
asks the agent to revise, up to `max_retries` times (default 4). The agent still holds its research
context, so it is fixing a citation, not redoing the work.

If retries are exhausted, the output is returned **annotated, not blocked**:

```
...the agent's final answer...

---
GOVERNANCE FLAGS:
[output-conformance]: Claim "response time improved 40%" cites no measurement.
  - response time improved 40%
```

So:

- **Write `reasoning` for the agent, not for a human.** It is the retry prompt. "Not grounded" is
  useless; "the 40% figure appears in no cited source — remove it or cite the measurement" is a
  fix.
- **A `fail` is not a stop.** If you need a hard stop, that is an approval gate, not a decision
  skill — see [Approval policy](../reference/approval-policy.md).
- **Set `max_retries` deliberately.** Four retries of a large answer through a judge is a real
  bill. `0` means judge once and annotate.

## Choosing the trigger

| Trigger | Fires | Use for |
|---|---|---|
| `pre_input` | before any LLM work | rejecting off-topic or malicious input — saves the whole run |
| `post_output` | after an agent's output | **validating the answer** |
| `checkpoint` | between task batches | catching a bad sub-result early in a long run |
| `pre_synthesis` | before the coordinator synthesises | judging task results before they are summarised |

`pre_synthesis` is the underrated one. It sees the raw task results and auto-loads `scope.json`
from run state as context, so it catches a wrong sub-result **before** synthesis launders it into
a fluent summary. Validating at both `pre_synthesis` and `post_output` costs two judge calls per
run — worth it when synthesis is where your topology goes wrong.

## Workspace-level vs topology-level

Same shape in `workspace.yaml`:

```yaml
governance:
  decision_skills:
    - id: output-conformance
      trigger: post_output
```

- **Workspace** — applies to every topology. A topology must explicitly opt out with
  `required: false`, which is visible in the artifact and therefore auditable.
- **Topology** — same `id` overrides the workspace binding, a new `id` extends it.

If the check is non-optional, bind it at the workspace. A topology-level binding is a check that
whoever writes the next topology can simply not add.

## Common mistakes

**Leaving `scope` at `*`.** Fires after every agent in the tree. Fine for a cheap grounding check,
expensive for a full conformance review.

**Putting validation logic in the compiler.** It belongs in the skill. A validation rule in Python
is one nobody can see, version, or change without a release.

**Judging quality instead of conformance.** A skill that asks "is this good?" fails good-but-plain
answers and burns four retries improving prose. Check grounding, scope and contradiction — things
with an answer.

**Assuming `fail` blocks.** It annotates. Design for that.

**Asking an LLM to check something a tool can decide.** If a validator, linter or schema can answer
it, bind the validator. The judge call is for the part that genuinely needs judgement.

**Wiring a validator without mapping its vocabulary.** `{"valid": false}` has no `verdict`, so the
parser defaults it to `pass` and the check reports success on every rejection.

## Checklist

- [ ] `output_schema` on the root agent covers the shape
- [ ] The decision skill's `outputs` declares `verdict` / `confidence` / `reasoning`
- [ ] `verdict` enum is exactly `pass` / `fail` / `needs-revision`
- [ ] `scope` names the agent whose output you mean
- [ ] `max_retries` is a number you chose, not the default you inherited
- [ ] `reasoning` reads as an instruction to the agent that will act on it
- [ ] Bound at the workspace if it must not be skippable
- [ ] Decidable checks use `mcp_tool`, not a prompt
- [ ] Any tool-backed skill emits `verdict` as exactly `pass` / `fail` / `needs-revision`
- [ ] A run with a deliberately unsupported claim actually gets flagged

## See also

- [Building swarms](building-swarms.md) — where skills and bindings sit in a topology.
- [Approval policy](../reference/approval-policy.md) — when a human, not a skill, has to decide.
- [Governed memory](../reference/governed-memory.md) — the same verdict vocabulary applied to
  memory writes.
