# Pre-input decision gate

## Goal

Validate user input before the agent performs any LLM work. A `pre_input` decision skill can reject off-topic or malicious queries early, saving tokens that would otherwise be wasted on irrelevant processing.

## Non-goals

- Not a rate limiter or auth mechanism — those are handled by governance and trust gates which fire first.
- Not a content filter for agent output — that's `post_output`.

## API

New trigger value `pre_input` added to the `DecisionSkillBinding.trigger` enum alongside `post_output`, `checkpoint`, and `pre_synthesis`.

```yaml
decision_skill_bindings:
  - id: relevance-gate
    trigger: pre_input
    scope: "*"
```

## Execution order

Within `_build_agent_node` in the compiler:

1. Governance gate (policy check — is the agent allowed to run?)
2. Trust gate (trust verification)
3. **Pre-input gate** (decision skills with `trigger: pre_input`)
4. Already-delegated fast path
5. Task plan execution
6. Message/tool construction + LLM call
7. Post-output gate (decision skills with `trigger: post_output`)

The pre_input gate fires after governance/trust (the agent must be allowed to run at all) but before any LLM call or delegation logic.

## Decision skill interface

The decision skill receives the user's input text as `content`. It returns:
- `verdict: "pass"` — proceed normally
- `verdict: "fail"` — reject the input

On failure, the gate extracts `suggested_response` from the result's `raw` dict (if the decision skill provided one) and returns it to the user. If no `suggested_response` is present, the skill's `reasoning` is used as the rejection message.

## Test plan

- `test_pre_input_gate.py`: filtering, pass-through, rejection with suggested response, rejection with reasoning fallback, scope filtering.
