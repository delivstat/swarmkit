---
title: Structured output governance + auto-correction
description: Deterministic output validation on skill results. Four-tier model. Field-specific errors enable targeted auto-correction re-prompts.
tags: [governance, output-validation, auto-correction, m4]
status: proposed
---

# Structured output governance + auto-correction

## Goal

When a skill declares an `outputs` block, the runtime enforces it
deterministically — before any LLM judge fires. Field-specific
validation errors are fed back to the model as targeted re-prompts,
fixing one field at a time instead of regenerating the entire response.

**Insight from Rynko gate validation:** structured constraints +
field-specific error feedback eliminates most hallucination at near-zero
cost. Shape-level errors (wrong type, missing field, out-of-range value)
don't need an LLM judge — they need a schema check + a re-prompt.

## Non-goals

- **Semantic evaluation.** "Is this reasoning correct?" is Tier 3 (LLM
  judge). This note covers Tiers 0–2 (structural/deterministic).
- **Free-text validation.** Output governance only fires for skills with
  declared `outputs` blocks. A root agent's final answer to the user is
  not schema-validated.
- **Changing the skill schema.** The existing `outputs` block in
  `skill.schema.json` is sufficient. This note defines how the runtime
  enforces it.

## The four-tier output governance model

| Tier | What | Cost | When |
|---|---|---|---|
| 0 | **Structured generation** — provider JSON mode / tool_use constrains the model at generation time | Zero extra | Always, when skill declares `outputs` |
| 1 | **Schema validation** — JSON Schema check on the response | Near-zero | Always, after model response |
| 2 | **Business rules** — deterministic field-level checks (ranges, enums, cross-field consistency) | Near-zero | When skill declares `validation_rules` |
| 3 | **LLM judge** — semantic evaluation against a rubric | Tokens | When configured (separate design note) |

Tiers 0–2 are this note. Tier 3 is `design/details/decision-skills.md`.

## Tier 0 — Structured generation

When a skill declares `outputs`, the compiler translates the output
schema into the provider's structured output mechanism:

| Provider | Mechanism |
|---|---|
| Anthropic | `tool_use` with the output schema as `input_schema` |
| OpenAI / Groq / OpenRouter | `response_format: { type: "json_schema", json_schema: {...} }` |
| Google | `response_mime_type: "application/json"` + `response_schema` |
| Ollama | `format: "json"` in options |

The model is structurally constrained to produce JSON matching the
schema. This eliminates shape-level hallucination at generation time.

**Implementation:** in `_build_agent_node`, when the agent has skills
with `outputs`, the `CompletionRequest` includes the output schema.
Each provider adapter translates to its native mechanism.

## Tier 1 — Schema validation

After the model returns a response, validate the parsed JSON against
the skill's `outputs` block using `jsonschema`:

```python
def _validate_skill_output(
    output: dict[str, Any],
    skill: ResolvedSkill,
) -> list[FieldError]:
    """Validate skill output against declared schema."""
    errors = []
    for field_name, field_spec in skill.outputs.items():
        if field_name not in output:
            errors.append(FieldError(field_name, "missing required field"))
            continue
        value = output[field_name]
        # Type check
        if field_spec.get("type") == "enum":
            if value not in field_spec.get("values", []):
                errors.append(FieldError(
                    field_name,
                    f"must be one of {field_spec['values']}, got '{value}'"
                ))
        elif field_spec.get("type") == "number":
            range_ = field_spec.get("range")
            if range_ and not (range_[0] <= value <= range_[1]):
                errors.append(FieldError(
                    field_name,
                    f"must be between {range_[0]} and {range_[1]}, got {value}"
                ))
        elif field_spec.get("type") == "string":
            if not isinstance(value, str):
                errors.append(FieldError(
                    field_name,
                    f"must be a string, got {type(value).__name__}"
                ))
    return errors
```

Each error is **field-specific** — it names the field and describes
exactly what's wrong.

## Tier 2 — Business rules

Skills can declare `validation_rules` for cross-field and domain-
specific checks:

```yaml
# skill.yaml
outputs:
  verdict:
    type: enum
    values: [pass, fail]
  confidence:
    type: number
    range: [0, 1]
  reasoning:
    type: string
validation_rules:
  - if: { verdict: "fail" }
    then: { confidence: { max: 0.5 } }
    message: "Failed verdict should have confidence <= 0.5"
  - field: reasoning
    min_length: 20
    message: "Reasoning must be at least 20 characters"
```

Rules are evaluated deterministically — no LLM, no tokens. Each rule
produces a field-specific error on failure.

**Implementation note:** `validation_rules` is a new optional field
on the skill schema. Adding it follows
`docs/notes/schema-change-discipline.md`.

## Auto-correction via field-specific errors

When Tier 1 or 2 validation fails, the errors are fed back to the
model as a targeted re-prompt:

```
Model returns: {"verdict": "pass", "confidence": 1.5, "reasoning": "ok"}

Validation errors:
  - confidence: must be between 0 and 1, got 1.5
  - reasoning: must be at least 20 characters

Re-prompt to model:
  "Your response had validation errors on these fields:
   - confidence: must be between 0 and 1, got 1.5
   - reasoning: must be at least 20 characters
   Correct ONLY these fields and return the full response."

Model returns: {"verdict": "pass", "confidence": 0.85, "reasoning": "The code follows all quality standards and has good test coverage."}

Validation: PASS
```

### Retry budget

Configurable per skill, defaults:

```yaml
# In the skill's runtime_config or workspace-level config
output_governance:
  max_retries: 2        # default
  escalate_on_failure: true  # escalate to Tier 3 or HITL
```

If the model can't produce valid output after `max_retries`:
1. If `escalate_on_failure` is true → escalate to Tier 3 (LLM judge)
   or HITL review queue
2. If false → return the last response with validation errors attached
   in metadata

### Why this works

- **Cheaper** than regenerating from scratch — the model corrects one
  or two fields, not the entire response
- **More reliable** than "try again" — the error is specific, not vague
- **Deterministic** in the validation step — no LLM judge cost for
  shape/range errors
- **Composable** with Tier 3 — structural errors are caught cheaply,
  semantic errors escalate to the judge

## Where this hooks into the compiler

In `_build_agent_node`, the tool-use loop (currently only handling
delegation) is extended:

```
1. Model call (with structured generation if skill has outputs)
2. Parse response
3. If tool_use:
   a. If delegation → route to child (existing)
   b. If skill with outputs → validate output:
      - Tier 1: schema validation
      - Tier 2: business rules
      - If valid → return result
      - If invalid → re-prompt with field errors (up to max_retries)
      - If exhausted → escalate or return with errors
   c. If skill without outputs → return result as-is
4. If text → return (no output governance on free-text)
```

## Implementation plan

### PR 1 (this PR): design note + output validator

- This design note
- `packages/runtime/src/swarmkit_runtime/skills/_output_validator.py`
  — `validate_skill_output()` function + `FieldError` dataclass
- Unit tests: valid output passes, type errors caught, range errors
  caught, enum errors caught, missing field caught

### PR 2: structured generation in providers

- Each provider adapter's `complete()` method accepts an output schema
  and translates to the provider's native mechanism
- `CompletionRequest` gains an `output_schema: dict | None` field
- Tests: mock provider returns JSON matching schema

### PR 3: auto-correction loop in compiler

- Wire `validate_skill_output()` into the compiler's agent node
- Re-prompt with field-specific errors on failure
- Retry budget (max_retries config)
- Integration test: mock model returns invalid output → correction →
  valid output

### PR 4: validation_rules schema extension

- Add `validation_rules` to skill schema (schema-change-discipline PR)
- Implement rule evaluation in `_output_validator.py`
- Tests for cross-field rules

## Test plan

- **Valid output passes all tiers.** Skill with outputs, model returns
  conforming JSON → no errors, no retry.
- **Type error triggers Tier 1.** Enum field gets wrong value → error
  names the field + expected values.
- **Range error triggers Tier 1.** Number field out of range → error
  names the field + range.
- **Missing field triggers Tier 1.** Required field absent → error
  names the field.
- **Cross-field rule triggers Tier 2.** `if verdict=fail then
  confidence<=0.5` — verdict is fail, confidence is 0.8 → error.
- **Auto-correction succeeds.** Mock model returns invalid on first
  call, valid on retry → final output is valid.
- **Retry budget exhaustion.** Mock model returns invalid every time →
  escalation event recorded, last response returned with errors.

## Exit demo

A skill with declared `outputs` schema:
1. Produces valid structured output on first attempt (structured
   generation constrains the model).
2. When given an intentionally malformed response (via mock), the
   auto-correction loop fixes the invalid field and succeeds on retry.
3. The retry prompt names the specific field and error — visible in
   audit events.
