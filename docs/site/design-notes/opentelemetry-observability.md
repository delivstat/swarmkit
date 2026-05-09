---
title: OpenTelemetry observability
description: OTel as the standard telemetry layer for the SwarmKit runtime, with OTLP as the protocol to Rynko and any third-party backend.
tags: [runtime, observability, telemetry]
status: draft
---

# OpenTelemetry observability

**Scope:** runtime
**Design reference:** §8.3 (audit log), §14 (runtime architecture), `product-architecture.md`
**Status:** draft

## Goal

Adopt OpenTelemetry as the telemetry standard for the SwarmKit runtime so that all agent execution data — traces, metrics, events — is emittable to any OTel-compatible backend, including the Rynko platform.

## Non-goals

- Building a custom telemetry protocol — OTel and OTLP exist, use them
- Deep instrumentation of every internal function — start with the semantic execution model, not code-level tracing
- Replacing the audit log — OTel traces and the governance audit log serve different purposes (observability vs compliance). They share data but have different retention and immutability guarantees

## Background

The product architecture (`product-architecture.md`) defines three deployment models. In all three, the runtime needs to emit structured telemetry. OTel is the vendor-neutral standard that makes this work:

- **CLI-only (free):** user sends OTel data to their own collector (Jaeger, Grafana, Datadog) or ignores it entirely
- **Cloud on Rynko:** runtime sends OTLP to Rynko's ingestion endpoint. Rynko adds the agent-aware semantic layer (drift analysis, governance visualization, cross-run analytics)
- **Enterprise self-hosted:** same OTLP, pointed at the customer's own Rynko instance or their existing observability stack

This resolves the open question in `product-architecture.md` about telemetry protocol: it's OTLP.

## Trace model

Each topology run maps to an OTel trace. The span hierarchy mirrors the agent execution model:

```
Trace: topology run (run_id)
├── Span: agent step (agent_id, step 1)
│   ├── Span: llm call
│   ├── Span: tool call (tool_name)
│   └── Span: governance check (decision: allow)
├── Span: agent step (agent_id, step 2)
│   ├── Span: llm call
│   ├── Span: tool call
│   └── Event: intent drift (score: 0.18)
├── Span: agent handoff (from → to)
├── Span: agent step (agent_id_2, step 1)
│   ├── Span: llm call
│   └── Span: approval gate (status: pending)
│       └── Event: human approval (approved_by, duration)
└── Span: topology complete (status: success)
```

## Semantic attributes

All SwarmKit-specific attributes live under the `swarmkit.*` namespace, following OTel semantic conventions.

### Trace-level attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `swarmkit.topology.id` | string | Topology identifier |
| `swarmkit.topology.version` | string | Topology version or git ref |
| `swarmkit.run.id` | string | Unique run identifier |
| `swarmkit.workspace.id` | string | Workspace identifier |

### Agent step span attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `swarmkit.agent.id` | string | Agent identifier within topology |
| `swarmkit.agent.archetype` | string | Archetype used |
| `swarmkit.agent.step` | int | Step number within agent execution |
| `swarmkit.model.provider` | string | Model provider used (anthropic, openai, etc.) |
| `swarmkit.model.id` | string | Model identifier |

### Tool call span attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `swarmkit.tool.name` | string | Tool or MCP server name |
| `swarmkit.tool.status` | string | success, error, timeout |
| `swarmkit.tool.error.type` | string | Error classification if failed |

### Governance span attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `swarmkit.governance.decision` | string | allow, deny, escalate |
| `swarmkit.governance.policy` | string | Policy that applied |
| `swarmkit.governance.scope` | string | IAM scope checked |

### Intent drift event attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `swarmkit.drift.score` | float | Drift score (0.0 = aligned, 1.0 = fully drifted) |
| `swarmkit.drift.threshold` | float | Configured threshold |
| `swarmkit.drift.action` | string | log, warn, nudge |
| `swarmkit.drift.exceeded` | bool | Whether threshold was exceeded |

### Approval gate span attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `swarmkit.approval.status` | string | pending, approved, rejected, timed_out |
| `swarmkit.approval.scope` | string | Scope requiring approval |
| `swarmkit.approval.wait_ms` | int | Time spent waiting for human |

## Metrics

Lightweight counters and histograms for operational monitoring. Emitted via OTel metrics API.

| Metric | Type | Description |
|--------|------|-------------|
| `swarmkit.runs.total` | counter | Total topology runs |
| `swarmkit.runs.duration_ms` | histogram | Run duration |
| `swarmkit.agent.steps.total` | counter | Total agent steps across all runs |
| `swarmkit.agent.drift.score` | histogram | Distribution of drift scores |
| `swarmkit.tool.calls.total` | counter | Tool invocations, by tool name and status |
| `swarmkit.tool.duration_ms` | histogram | Tool call latency |
| `swarmkit.governance.decisions.total` | counter | Governance decisions, by decision type |
| `swarmkit.approval.wait_ms` | histogram | Human approval wait time |

## Runtime configuration

```yaml
# ~/.swarmkit/config.yaml
telemetry:
  enabled: true
  exporter: otlp              # otlp | console | none
  endpoint: https://api.rynko.dev/v1/traces
  protocol: grpc               # grpc | http
  api_key: rk-...              # Rynko API key, or omit for third-party backends
  headers: {}                  # additional headers for custom collectors
  sample_rate: 1.0             # 1.0 = all traces, 0.1 = 10% sampling
  send_prompts: false          # opt-in: include LLM prompt/response content as span events
```

Default: `exporter: none`. Telemetry is opt-in. When a user adds a Rynko API key via the upgrade path, the exporter switches to `otlp` pointing at Rynko's endpoint.

The `console` exporter prints spans to stderr in a human-readable format — useful for local debugging without any external collector.

## What Rynko adds on top of raw OTel

Raw OTel data gives you generic trace visualization (Jaeger, Grafana Tempo). Rynko's value is the agent-aware semantic layer built on top of the same data:

- **Topology-aware trace view** — spans rendered as an agent flow diagram, not just a waterfall
- **Intent drift visualization** — drift scores plotted over the run timeline, with nudge events highlighted
- **Cross-run analytics** — "this agent's mean drift improved 15% over the last 50 runs"
- **Learned profiles** — `threshold: auto` derived from historical OTel data (see `intent-drift-detection.md`)
- **Governance timeline** — approval gates, policy decisions, escalations rendered as a decision audit trail
- **Cost attribution** — LLM token usage per agent per step, aggregated across runs
- **Alerting** — threshold-based alerts on drift, latency, error rates, approval wait times

This is the commercial differentiator: OTel data is free and portable, the intelligence on top of it is what Rynko sells.

## Implementation approach

Start lightweight, instrument deeper as the runtime stabilizes:

### Phase 1 (with runtime v1.0)

- Trace-per-run, span-per-agent-step
- Tool call and governance check child spans
- Key semantic attributes (agent ID, archetype, tool name, governance decision)
- Console and OTLP exporters
- `send_prompts: false` by default

### Phase 2 (with intent drift)

- Drift scores as span events
- Drift-related attributes and metrics
- Approval gate spans with wait time

### Phase 3 (with Rynko integration)

- Full metrics suite
- Cost attribution (token counts as span attributes)
- Rynko-specific ingestion optimizations (batching, compression)
- Sampling strategies for high-volume topologies

## API shape

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

class SwarmKitTelemetry:
    def __init__(self, config: TelemetryConfig) -> None: ...
    def start_run(self, topology_id: str, run_id: str) -> trace.Span: ...
    def start_agent_step(self, agent_id: str, step: int) -> trace.Span: ...
    def record_tool_call(self, tool_name: str, status: str) -> trace.Span: ...
    def record_governance_decision(self, decision: str, policy: str) -> None: ...
    def record_drift(self, score: float, threshold: float, action: str) -> None: ...
    def record_approval(self, status: str, scope: str, wait_ms: int) -> None: ...
```

The telemetry layer is injected into the runtime via dependency injection — not imported directly by agent execution code. Agent code never calls OTel APIs; the runtime wrapper instruments around it.

## Test plan

- Unit: span hierarchy correctness (run → agent step → tool call), attribute presence
- Unit: exporter configuration (console, otlp, none)
- Unit: `send_prompts` flag respected — no prompt content in spans when false
- Integration: full topology run produces a valid OTel trace exportable to a local Jaeger instance
- Test data: sample topologies with known step counts and tool calls

## Demo plan

Run a reference topology with `telemetry.exporter: console`, show the span output in the terminal. Optionally spin up a local Jaeger via Docker and show the trace visualization.

## Local ring buffer — privacy-first prompt debugging

Raw LLM prompts and responses never leave the user's environment. To maintain a high-quality debugging experience in the Rynko UI without compromising privacy, the runtime uses a persistent local ring buffer.

- **Storage:** local SQLite database (not in-memory). Must survive process restarts — overnight batch jobs fail, developer debugs the next morning.
- **Keyed by:** OTel `span_id` and `run_id`, linking local debug data to cloud trace visualization.
- **Retention:** configurable time-to-live (default: last 7 days) or run-count limit, whichever is larger.
- **Retrieval via CLI:**

```bash
swarmkit debug --span-id <id>              # prompt/response for a specific span
swarmkit debug --run-id <id>               # all prompts for a run
swarmkit debug --agent researcher --last 5 # last 5 steps for an agent
```

- **Privacy guarantee:** the ring buffer is local-only. Rynko never receives prompt content unless `send_prompts: true` is explicitly set in the telemetry config.

This enables a "Zero-Trust AI Ops" positioning — enterprises get a collaborative debugging UI in Rynko without proprietary data ever leaving their VPC. The Rynko dashboard shows the structural OTel trace; the CLI pulls the sensitive content from local storage when needed.

## Transport recommendation

Start with **OTLP/HTTP** using asynchronous batching. Advantages over gRPC:

- Keeps the runtime lightweight
- Avoids connection-drop headaches in diverse network environments (enterprise firewalls, proxies)
- Makes Rynko instantly compatible with the broader observability ecosystem

Move to gRPC only when payload size or velocity becomes a bottleneck.

## Open questions

- Should the audit log (§8.3) be derived from OTel traces, or remain a separate system that shares data? The audit log has immutability guarantees that OTel storage may not.
- Span event vs child span for drift scores — events are lighter but less visible in trace UIs
- Whether to include a `swarmkit.cost.tokens` attribute on LLM call spans (requires model provider cooperation)
- How granular should the prompt opt-in be? Per-topology? Per-agent? Per-run? Per-MCP-server?
- Should the local ring buffer support a "secure tunnel" mode where the Rynko UI can pull prompts on-demand from the runtime (with user approval), or is CLI-only access sufficient?
