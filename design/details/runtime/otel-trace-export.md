# OTel trace export — wire the runtime to emit spans

Status: accepted. Implements the trace-emission half of the (draft) OpenTelemetry design
(`opentelemetry-observability.md`): every topology run becomes an OTel trace, exported to the
configured collector so Jaeger/Grafana actually show runs.

## The gap

The `telemetry/` package already has the whole facade — `SwarmKitTelemetry` (start_run /
start_agent_step / start_tool_call / record_model_usage), the config loader
(`SWARMKIT_OTEL_EXPORTER` / `SWARMKIT_OTEL_ENDPOINT`), and semantic attributes. But **nothing ever
constructs it or emits a span** on the run path: `load_telemetry_config` and `SwarmKitTelemetry` are
never called outside their own package. So `SWARMKIT_OTEL_EXPORTER=otlp` is a no-op — Jaeger stays
empty. The real tracing today is the in-memory `RunTrace`, saved as `.swarmkit/traces/<id>.json`.

## Approach — bridge the RunTrace, don't re-instrument

The compiler already records everything into `RunTrace` (run → `agent_steps` → `tool_calls`, with
timings, tokens, cost, errors). Rather than thread a tracer through the hot path and double-record,
**emit the OTel span tree from the finished `RunTrace` at run end** — one integration point, zero
change to agent execution, and Jaeger mirrors the on-disk trace exactly. Spans carry their recorded
start/end timestamps, so the Jaeger timeline is accurate even though export happens at run end.

Span tree (matches the design's hierarchy):

```
topology.run                (run_id, topology, workspace, total tokens, total cost, llm_calls)
├── agent.step.<agent_id>   (agent_id, model, role, tokens_in/out, cost; ERROR status if it failed)
│   └── tool.call.<name>    (tool_name, result_length, cached; ERROR status if it errored)
└── agent.step.<agent_id_2> …
```

`ToolCall` has only `duration_ms` (no absolute start), so tool spans are laid out **sequentially**
within their agent step's window (cursor starts at the step start; each tool advances it by its
duration). Tool durations are exact; inter-tool gaps (LLM thinking) are approximate — good enough for
a waterfall.

## Pieces

1. **`SwarmKitTelemetry.export_run_spans(root)`** — emit a tree of `RecordedSpan(name, start_ns,
   end_ns, attributes, error, children)` via the raw tracer with explicit `start_time`/`end_time` and
   parent nesting (`start_span(context=…, start_time=…)` + `span.end(end_time=…)`). No-op when
   disabled. `__init__` gains an optional injected `provider` (for tests — an in-memory exporter).
2. **Active singleton** — `get_telemetry()` lazily builds one `SwarmKitTelemetry` from
   `load_telemetry_config()` (covers both `serve` and CLI `run`); `configure_telemetry()` sets it
   eagerly. The OTel `TracerProvider` is process-global, so this guards single setup.
3. **Bridge at run end** — in `WorkspaceRuntime.run()`, after `trace.finish()`, if telemetry is
   enabled, convert `RunTrace` → `RecordedSpan` tree and call `export_run_spans`. Best-effort — a
   telemetry failure never fails a run.
4. **Serve startup** — the server lifespan calls `configure_telemetry()` and logs the exporter +
   endpoint (so an operator sees "otlp → http://…"), instead of the silent no-op today.

## Non-goals

- No live/streaming spans; export is post-hoc from `RunTrace` (accurate timeline, simpler, safe).
- No new metrics (the metrics helpers already exist); this is traces only.
- No governance/handoff sub-spans yet — run/agent/tool covers the ask; events can follow.

## Test plan

- `export_run_spans` emits a correctly-nested tree (in-memory exporter): one run span, N agent
  spans as children, tool spans under the right agent; timestamps + attributes (tokens/cost) set;
  error status propagates.
- Disabled telemetry → `export_run_spans` is a no-op (no spans, no raise).
- The `RunTrace` → `RecordedSpan` conversion nests tools in their step and lays them out in order.
- `get_telemetry()` returns a singleton.

## Demo

`SWARMKIT_OTEL_EXPORTER=console` run of a topology prints the span tree; against the observability
compose (`SWARMKIT_OTEL_EXPORTER=otlp`, endpoint :4318) a run shows up in Jaeger under the workspace
service — the live kimi run that previously left Jaeger empty.
