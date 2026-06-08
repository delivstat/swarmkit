# Level 7: Governance & Safety

Add guardrails that structurally prevent agents from going wrong — not through prompting, but through enforced policy gates.

## What you'll learn

- Decision skills as pre/post gates
- IAM scopes and trust levels
- Circuit breakers (cost/step limits)
- Output schema validation
- Gate validators (drop-in JSON Schema)
- Human-in-the-loop review queues
- Audit trail configuration

## Why governance?

Prompt instructions are suggestions — agents can ignore them. Governance is structural — the runtime enforces it before the agent can act. "Don't access production data" as a prompt is hopeful. `base_scope: [data:read-staging]` without `data:read-production` is enforced.

## Build it

### 1. Decision skills as gates

Decision skills run before (pre_input) or after (post_output) every agent turn:

```yaml
# skills/content-filter.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: content-filter
  name: Content Filter
  description: >
    Blocks harmful, offensive, or off-topic content before
    it reaches the agent.
category: decision
implementation:
  type: llm_prompt
  prompt: |
    Evaluate this user input for safety:

    INPUT: {{input}}

    Check for:
    - Violence or threats
    - Hate speech or discrimination
    - Requests for illegal activity
    - Personal information exposure

    Return JSON:
    {
      "verdict": "pass" or "fail",
      "reasoning": "brief explanation"
    }
output_schema:
  type: object
  required: [verdict, reasoning]
  properties:
    verdict:
      type: string
      enum: [pass, fail, needs-review]
    reasoning:
      type: string
provenance:
  authored_by: human
  version: 1.0.0
```

### 2. Bind decision skills in workspace.yaml

```yaml
# workspace.yaml — updated governance
governance:
  provider: mock
  decision_skills:
    # Runs BEFORE every agent turn — blocks unsafe input
    - id: content-filter
      trigger: pre_input
      scope: "*"              # applies to all agents

    # Runs AFTER every agent turn — checks output quality
    - id: quality-check
      trigger: post_output
      scope: "*"
      config:
        min_confidence: 0.7   # skill-specific config
```

`trigger: pre_input` runs before the agent sees the input. `trigger: post_output` runs after the agent responds — if the verdict is "fail", the response is regenerated.

### 3. IAM scopes

Control what each agent can access:

```yaml
# archetypes/researcher.yaml — with IAM
defaults:
  # ...model and prompt...
  iam:
    base_scope:
      - knowledge:read        # can search knowledge bases
      - files:read            # can read files
    # elevated_scopes:
    #   - files:write         # would need explicit approval
```

```yaml
# archetypes/writer.yaml — different scopes
defaults:
  iam:
    base_scope:
      - knowledge:read
      - files:read
      - files:write           # writers can create files
```

Skills declare what scopes they need:

```yaml
# skills/write-file.yaml
implementation:
  type: mcp_tool
  server: filesystem
  tool: write_file
iam:
  required_scopes:
    - files:write             # must have this scope to use this skill
```

If an agent without `files:write` tries to use the `write-file` skill, governance blocks it.

### 4. Circuit breakers

Prevent runaway costs and infinite loops:

```yaml
# workspace.yaml — circuit breakers
governance:
  provider: mock
  limits:
    max_steps_per_agent: 20     # abort if one agent exceeds 20 steps
    max_steps_per_run: 100      # abort if total run exceeds 100 steps
    max_cost_per_run_usd: 1.00  # abort if estimated cost exceeds $1
```

### 5. Output schema validation

Force agents to return structured output:

```yaml
# In a topology or archetype
agents:
  root:
    id: analyst
    role: root
    archetype: researcher
    output_schema:
      type: object
      required: [findings, confidence, recommendation]
      properties:
        findings:
          type: array
          items:
            type: string
        confidence:
          type: number
          minimum: 0
          maximum: 1
        recommendation:
          type: string
          enum: [approve, reject, needs-review]
```

The compiler enforces JSON output matching this schema. If the model doesn't produce valid JSON, it retries with corrections.

### 6. Gate validators (drop-in)

Create JSON Schema files in a `gates/` directory — any agent output matching the schema is validated:

```bash
mkdir gates
```

```json
// gates/safe-output.schema.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "content": {
      "type": "string",
      "maxLength": 10000
    }
  },
  "required": ["content"]
}
```

Reference it as a decision skill:

```yaml
governance:
  decision_skills:
    - id: gate-validator
      trigger: post_output
      scope: "*"
      config:
        gate_id: "safe-output"
```

### 7. Human-in-the-loop review

When a decision skill returns `needs-review`, the item enters a review queue:

```bash
# List pending reviews
swarmkit review list .

# Show details
swarmkit review show <review-id> .

# Approve or reject
swarmkit review approve <review-id> .
swarmkit review reject <review-id> .
```

### 8. Audit trail

All governance decisions are logged:

```yaml
# Per-skill audit configuration
audit:
  log_inputs: summary        # full | summary | none
  log_outputs: full
  redact: ["$.password", "$.api_key", "$.token"]
```

View audit events:

```bash
swarmkit logs . --last 5
swarmkit status .
```

### 9. Topology-level overrides

Override workspace governance for a specific topology:

```yaml
# topologies/sensitive-task.yaml
runtime:
  governance:
    decision_skills:
      - id: content-filter
        trigger: pre_input
        scope: "*"
      - id: quality-check
        trigger: post_output
        scope: "*"
      # Additional gate for this topology only
      - id: pii-detector
        trigger: post_output
        scope: "*"
```

## Your workspace so far

```
my-swarm/
├── workspace.yaml          # governance, decision_skills, limits
├── archetypes/             # with IAM scopes
├── skills/
│   ├── content-filter.yaml # pre_input gate
│   ├── quality-check.yaml  # post_output gate
│   └── ...
├── gates/
│   └── safe-output.schema.json
├── servers/
└── topologies/
```

## Next

[Level 8: Observability](08-observability.md) — trace what your agents are doing, detect drift, and debug failures.
