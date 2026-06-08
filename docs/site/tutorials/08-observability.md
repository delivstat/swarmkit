# Level 8: Observability & Debugging

See everything your agents do — traces, metrics, drift detection, and the ability to ask "what happened?" after a run.

## What you'll learn

- Run tracing with `swarmkit trace`
- Debugging with `swarmkit logs`, `why`, `ask`, `debug`
- Intent drift detection
- OpenTelemetry integration
- Prompt ring buffer
- Tool call recording

## Observability commands

SwarmKit provides six CLI commands for understanding what happened:

### 1. Status — quick overview

```bash
swarmkit status .
```

Shows recent runs with timing and outcomes. Quick "is everything working?" check.

### 2. Logs — detailed events

```bash
# Last 3 runs
swarmkit logs . --last 3

# Filter by topology
swarmkit logs . --topology content-team

# Filter by agent
swarmkit logs . --agent researcher

# Specific run
swarmkit logs . --run-id abc123

# Markdown format for reports
swarmkit logs . --last 1 --format markdown
```

### 3. Trace — agent call graph

```bash
# List recent runs with token counts
swarmkit trace -w .

# Full call graph for a specific run
swarmkit trace <run-id> -w .
```

Shows the agent hierarchy, which tools each called, token counts per agent and per model, and timing.

### 4. Why — LLM explains what happened

```bash
swarmkit why <run-id> .
```

Uses an LLM to analyze the run's events and explain what happened in plain language. Great for debugging unexpected outputs.

### 5. Ask — conversational observer

```bash
# Ask about the workspace
swarmkit ask "Which agents use the most tokens?" -w .

# Ask about a specific run
swarmkit ask "Why did the security reviewer flag this code?" -w . --run <run-id>
```

An LLM-powered observer that answers questions about your workspace and runs.

### 6. Debug — retrieve raw prompts

```bash
# See what was actually sent to the model
swarmkit debug . --run-id <run-id>

# Specific agent's prompts
swarmkit debug . --agent researcher --last 3

# Specific span
swarmkit debug . --span-id <span-id>
```

Retrieves the actual prompts and responses from the local ring buffer. No data leaves your machine.

## Intent drift detection

Agents can wander from their original goal. Drift detection catches this:

```yaml
# topologies/content-team.yaml — add drift monitoring
agents:
  root:
    id: coordinator
    role: root
    archetype: coordinator
    intent_monitoring:
      enabled: true
      threshold: 0.75       # 0.5 = aggressive, 0.75 = balanced, 0.9 = permissive
      on_drift: nudge       # log | warn | nudge
```

| Action | Behavior |
|--------|----------|
| `log` | Record drift score in audit, continue |
| `warn` | Log + emit event for monitoring |
| `nudge` | Inject a message: "You're drifting from the goal. Refocus on: {original_input}" |

The drift score uses cosine similarity between the original input and each agent's output. If the score drops below the threshold, the configured action fires.

## OpenTelemetry

Export traces and metrics to any OTel backend:

```bash
# Console output (development)
SWARMKIT_OTEL_EXPORTER=console swarmkit run . content-team --input "Test"

# OTLP endpoint (production — Grafana, Jaeger, etc.)
SWARMKIT_OTEL_ENDPOINT=http://localhost:4318 swarmkit run . content-team --input "Test"
```

Built-in metrics:
- `swarmkit.runs.total` — run counter
- `swarmkit.runs.duration_ms` — run duration histogram
- `swarmkit.agent.steps.total` — agent step counter
- `swarmkit.tool.calls.total` — tool call counter
- `swarmkit.tool.duration_ms` — tool call duration
- `swarmkit.governance.decisions.total` — governance decision counter
- `swarmkit.agent.drift.score` — drift score histogram
- `swarmkit.agent.drift.breaches.total` — drift threshold breaches

## Tool call recording

Every MCP tool call is recorded in the run trace:

```json
{
  "tool_calls": [
    {
      "tool_name": "brain-search",
      "arguments": {"query": "karma"},
      "result_length": 15234,
      "duration_ms": 120,
      "error": null
    }
  ]
}
```

View in the web UI's trace panel (below each chat message) or via `swarmkit trace <run-id>`.

## Prompt ring buffer

All prompts and responses are stored locally in `.swarmkit/prompts.sqlite`. This never leaves your machine — it's for debugging only.

```bash
# What did the model actually see?
swarmkit debug . --agent researcher --last 1
```

Shows the full system prompt, user messages, and model response for that agent's last invocation.

## Your workspace so far

```
my-swarm/
├── workspace.yaml
├── archetypes/
├── skills/
├── servers/
├── gates/
└── topologies/
    └── content-team.yaml   # now with intent_monitoring
```

## Next

[Level 9: Conversations & Memory](09-conversations-memory.md) — multi-turn chat and agents that remember.
