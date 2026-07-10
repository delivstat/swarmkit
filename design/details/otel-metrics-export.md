---
status: draft
---

# OTel metrics export — wiring the runtime meter to the fleet

Companion to `opentelemetry-observability.md` (which specifies the metric set) and the trace-export
wiring already shipped. This note covers only the missing half: getting the runtime to actually
**emit** OTel metrics so Prometheus/Grafana populate.

## The gap this closes

The metric instruments (`swarmkit.runs.total`, `swarmkit.agent.steps.total`,
`swarmkit.tool.calls.total`, durations, drift, compression) are defined in
`telemetry/_metrics.py`, and the fleet's observability stack is fully wired to receive them —
`deploy/observability/otel-collector-config.yaml` has a `metrics` pipeline (`otlp → prometheus:8889`
with `resource_to_telemetry_conversion` so `service.name → service_name`), Prometheus scrapes it, and
`grafana/dashboards/swarmkit.json` queries `swarmkit_runs_total` et al.

But the **runtime never emits a metric**:

1. **No `MeterProvider` is ever configured.** `init_metrics()` calls `metrics.get_meter(...)`, which
   returns a no-op meter unless a real provider is set — nothing sets one. (Traces got a
   `TracerProvider`; metrics were left as a stub.)
2. **The `record_*` helpers are never called** on the run path.

Result: Prometheus has zero `swarmkit_*` series and every Grafana panel is empty. Traces work
(BatchSpanProcessor → Jaeger); metrics were scaffolded and never connected.

## Goal

When telemetry is enabled (`exporter=otlp|console`), the runtime configures a `MeterProvider` and
emits run/step/tool metrics for every topology run, so the existing Grafana dashboard populates with
no config change to the collector, Prometheus, Grafana, or the dashboard.

## Non-goals

- No new metric instruments — the set in `_metrics.py` / the design note stands.
- No collector/Prometheus/Grafana changes — those are already correct.
- The dashboard's three headline series (runs, agent steps, tool calls) were the initial acceptance
  bar. **Follow-up (landed):** the remaining defined instruments are now wired too —
  `swarmkit.governance.decisions.total` at the three `evaluate_action` seams (agent-execute,
  mcp-call, context-retrieve; labelled `decision=allow|deny`, coarse `scope` to bound cardinality)
  and `swarmkit.approval.wait_ms` in `FileReviewQueue.resolve` (wait = resolve time − the review
  item's `timestamp`). Drift and compression metrics already had call sites and simply revived once
  the `MeterProvider` existed. Two Grafana panels (governance allow-vs-deny rate; approval-wait p95)
  cover the new series.

## Design

**Symmetry with traces.** Metrics are wired exactly where traces are, and emitted from the same
finished `RunTrace` — a post-hoc bridge, not scattered `record_*` calls across the executor.

1. **Provider setup** (`SwarmKitTelemetry.__init__`, alongside the `TracerProvider`): for
   `exporter=otlp`, build a `MeterProvider(resource=<same service.name resource>,
   metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=<metrics endpoint>),
   export_interval_millis=15000)])`; for `console`, a `ConsoleMetricExporter`. Then
   `init_metrics(service_name, meter_provider=<that provider>)` so the module instruments bind to it
   (passing the provider explicitly sidesteps OTel's set-once global-provider semantics and makes the
   path unit-testable with an in-memory reader).

2. **Metrics endpoint derivation.** The OTLP traces endpoint (`SWARMKIT_OTEL_ENDPOINT`, default
   `…/v1/traces`) is mapped to the metrics endpoint by replacing the trailing `/v1/traces` with
   `/v1/metrics`. One collector receives both. No new env var.

3. **Emission** (`export_run_metrics(trace)`, called from `_finalize_trace` right after
   `export_run_spans`): increments `runs.total` + records `runs.duration_ms`; per agent step,
   `agent.steps.total`; per tool call, `tool.calls.total` + `tool.duration_ms` (status `ok|error`);
   compression aggregates when present. Same `contextlib.suppress` best-effort framing as the span
   export — **telemetry never fails or slows a run** (the reader exports on its own 15s interval off
   the hot path).

4. **`service_name` label.** The `MeterProvider` uses the same `Resource(service.name=…)` as the
   tracer, and the serve lifespan already sets that to `"<workspace name> (<id>)"`. The collector's
   `resource_to_telemetry_conversion` promotes it to the `service_name` Prometheus label, so the
   dashboard's `sum by (service_name)` works per instance.

## API shape

- `init_metrics(service_name, *, meter_provider=None)` — bind instruments to an explicit provider
  (or the global).
- `SwarmKitTelemetry.export_run_metrics(trace)` — emit all metrics for a finished `RunTrace`;
  no-op when telemetry is disabled.
- `SwarmKitTelemetry(config, *, meter_provider=…)` — inject a provider for tests (mirrors the
  existing `provider=` trace injection).

## Test plan

- **Unit (instruments):** build a `MeterProvider` with `InMemoryMetricReader`, `init_metrics(...,
  meter_provider=...)`, call `record_run_started/agent_step/tool_call`, assert the reader exposes
  `swarmkit_runs_total`, `swarmkit_agent_steps_total`, `swarmkit_tool_calls_total` with expected
  values + labels.
- **Unit (bridge):** `export_run_metrics(RunTrace fixture)` with steps + tool calls → assert counts
  match the trace (N steps, M tool calls, 1 run, duration recorded).
- **Disabled:** `exporter=none` → `export_run_metrics` is a no-op, no provider set, no throw.

## Demo plan

Against the live fleet: run one cheap topology on an instance, wait for the 15s export + Prometheus
scrape, then show `curl 'localhost:9090/api/v1/query?query=swarmkit_runs_total'` returns a series
labelled with the instance's `service_name` — i.e. the Grafana SwarmKit dashboard goes from empty to
populated.
