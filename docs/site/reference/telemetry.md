# Telemetry configuration

SwarmKit uses OpenTelemetry for runtime observability. Traces capture every topology run, agent step, tool call, and governance decision as structured spans with `swarmkit.*` semantic attributes.

Telemetry is **disabled by default** — zero overhead until you opt in.

## Quick start

### Local testing (console output)

```bash
SWARMKIT_OTEL_EXPORTER=console swarmkit run my-swarm/ my-topology --input "hello"
```

Spans print to stderr in human-readable format.

### Local Jaeger

```bash
# Start Jaeger
docker run -d --name jaeger -p 4318:4318 -p 16686:16686 jaegertracing/all-in-one

# Run with OTLP export
SWARMKIT_OTEL_EXPORTER=otlp SWARMKIT_OTEL_ENDPOINT=http://localhost:4318/v1/traces \
  swarmkit run my-swarm/ my-topology --input "hello"

# View traces at http://localhost:16686
```

## Configuration

### Environment variables (for testing / CI / Docker)

| Variable | Description | Example |
|---|---|---|
| `SWARMKIT_OTEL_EXPORTER` | Exporter type: `console`, `otlp`, or `none` | `console` |
| `SWARMKIT_OTEL_ENDPOINT` | OTLP collector URL | `http://localhost:4318/v1/traces` |
| `SWARMKIT_OTEL_API_KEY` | API key (sent via configured header) | `rk-abc123` |
| `SWARMKIT_OTEL_HEADERS` | Comma-separated key=value pairs | `x-custom=val,x-org=acme` |

Env vars override config file values when set. Setting `SWARMKIT_OTEL_EXPORTER` to anything other than `none` automatically enables telemetry.

### Config file (for production)

Location: `~/.swarmkit/config.yaml`

```yaml
telemetry:
  enabled: true
  exporter: otlp                              # otlp | console | none
  endpoint: https://api.rynko.dev/v1/traces   # OTLP/HTTP endpoint
  api_key: rk-your-api-key                    # sent via api_key_header
  api_key_header: Authorization               # which header carries the key
  headers:                                    # additional headers (optional)
    x-org-id: acme-corp
  sample_rate: 1.0                            # 1.0 = all traces, 0.1 = 10%
  send_prompts: false                         # opt-in: include LLM prompt text in spans
  service_name: swarmkit                      # OTel service.name resource attribute
```

### Resolution order

1. **Environment variables** — highest priority, for quick overrides
2. **Config file** (`~/.swarmkit/config.yaml`) — for persistent production settings
3. **Defaults** — disabled, zero overhead

## Authentication

Different backends expect different authentication headers:

### Rynko (default)

```yaml
telemetry:
  endpoint: https://api.rynko.dev/v1/traces
  api_key: rk-your-key
  # api_key_header defaults to "Authorization"
  # Key is sent as: Authorization: Bearer rk-your-key
```

### Grafana Cloud

```yaml
telemetry:
  endpoint: https://otlp-gateway-prod-us-east-0.grafana.net/otlp/v1/traces
  api_key: "your-instance-id:your-api-token"
  # Sent as: Authorization: Basic <base64>
  # Or use headers directly:
  headers:
    Authorization: "Basic dXNlcjpwYXNz..."
```

### Honeycomb

```yaml
telemetry:
  endpoint: https://api.honeycomb.io/v1/traces
  api_key: hcaik_your_key
  api_key_header: x-honeycomb-team
  # Sent as: x-honeycomb-team: hcaik_your_key (no Bearer prefix)
```

### Custom collector (no auth)

```yaml
telemetry:
  endpoint: http://otel-collector.internal:4318/v1/traces
  # No api_key needed
```

The `api_key_header` field controls which header receives the API key:
- When `api_key_header: Authorization` (default), the key is prefixed with `Bearer `
- For any other header name, the key is sent raw (no prefix)
- If `headers` already contains the target header, `api_key` is not added (explicit headers win)

## Multiple workspaces

Multiple workspaces can send traces to the same backend. Traces are distinguished by span attributes:

- `swarmkit.workspace.id` — workspace identifier
- `swarmkit.topology.id` — which topology ran
- `swarmkit.run.id` — unique run identifier

Query your backend with these attributes to filter by workspace or topology.

## Semantic attributes

All SwarmKit spans use the `swarmkit.*` attribute namespace:

### Trace-level (topology run)

| Attribute | Type | Description |
|---|---|---|
| `swarmkit.topology.id` | string | Topology name |
| `swarmkit.run.id` | string | Unique run identifier |
| `swarmkit.workspace.id` | string | Workspace identifier |

### Agent step spans

| Attribute | Type | Description |
|---|---|---|
| `swarmkit.agent.id` | string | Agent identifier |
| `swarmkit.agent.step` | int | Step number |
| `swarmkit.agent.archetype` | string | Archetype used |
| `swarmkit.agent.role` | string | root / leader / worker |

### Tool call spans

| Attribute | Type | Description |
|---|---|---|
| `swarmkit.tool.name` | string | Tool or MCP server tool name |
| `swarmkit.tool.server` | string | MCP server ID |
| `swarmkit.tool.status` | string | success / error / timeout |
| `swarmkit.tool.error.type` | string | Error classification |

### Model usage (on agent spans)

| Attribute | Type | Description |
|---|---|---|
| `swarmkit.model.provider` | string | anthropic / openai / google / ollama |
| `swarmkit.model.id` | string | Model identifier |
| `swarmkit.model.tokens_in` | int | Input tokens |
| `swarmkit.model.tokens_out` | int | Output tokens |
| `swarmkit.model.cost_usd` | float | Estimated cost |

### Governance events

| Attribute | Type | Description |
|---|---|---|
| `swarmkit.governance.decision` | string | allow / deny |
| `swarmkit.governance.policy` | string | Policy that applied |
| `swarmkit.governance.scope` | string | IAM scope checked |

### Intent drift events (M7)

| Attribute | Type | Description |
|---|---|---|
| `swarmkit.drift.score` | float | 0.0 (aligned) to 1.0 (fully drifted) |
| `swarmkit.drift.threshold` | float | Configured threshold |
| `swarmkit.drift.action` | string | log / warn / nudge |
| `swarmkit.drift.exceeded` | bool | Whether threshold was breached |

## Span hierarchy

```
Trace: topology.run (swarmkit.topology.id, swarmkit.run.id)
├── Span: agent.step.supervisor (step=1)
│   ├── Event: governance.decision (allow)
│   └── Span: tool.call.delegate_to_worker
├── Span: agent.step.worker (step=1)
│   ├── Span: tool.call.github-pr-read
│   │   └── status: success
│   ├── Event: governance.decision (allow)
│   └── Event: intent.drift (score=0.12)
└── Span: agent.step.supervisor (step=2)
    └── status: completed
```

## Privacy

- `send_prompts: false` (default) — no LLM prompt/response content in spans
- When false, prompts are stored only in the local ring buffer (`.swarmkit/prompts.sqlite`) keyed by span ID — never sent to the telemetry backend
- `send_prompts: true` — opt-in, includes prompt text as span events (for debugging when privacy is not a concern)

## Governance circuit breakers

Circuit breakers prevent runaway agent execution and cost overruns. They're enforced inside the runtime — not at the billing layer — so they abort immediately when a limit is exceeded.

### Configuration

Add a `limits` block to the `governance` section in `workspace.yaml`:

```yaml
governance:
  provider: agt
  limits:
    max_steps_per_agent: 20      # per individual agent
    max_steps_per_run: 200       # total across all agents
    max_cost_per_run_usd: 5.00   # estimated LLM cost cap
```

### Limits

| Limit | Default | Description |
|---|---|---|
| `max_steps_per_agent` | unlimited | Maximum execution steps for any single agent |
| `max_steps_per_run` | 500 | Maximum total steps across all agents in one run |
| `max_cost_per_run_usd` | unlimited | Maximum estimated LLM cost (USD) per run |

### Behavior

When a limit is exceeded, the runtime raises `CircuitBreakerError` with a clear message:

```
Circuit breaker triggered: max_steps_per_run exceeded (limit=200, actual=201).
Configure governance.limits.max_steps_per_run in workspace.yaml to adjust.
```

The error names the specific limit, shows the actual vs allowed value, and tells the user which config to change.

### Use cases

- **Prevent infinite loops:** two agents arguing back and forth hit `max_steps_per_run` and abort
- **Cost control:** a topology running against an expensive model hits `max_cost_per_run_usd` before burning through the budget
- **Agent isolation:** a single misbehaving agent hitting `max_steps_per_agent` doesn't take down the whole run

## MCP server trace propagation (future)

Currently, the SwarmKit runtime creates spans *around* MCP tool calls. The MCP server process itself does not contribute child spans to the trace.

Future enhancement: inject W3C `traceparent` context into MCP calls (HTTP header for SSE transport, env var for stdio transport) so that MCP servers with their own OTel instrumentation produce linked child spans in the same trace.
