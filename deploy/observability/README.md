# SwarmKit fleet observability bundle

A turnkey OpenTelemetry stack for a SwarmKit fleet: an **OTel Collector** that receives OTLP from
every instance and fans out to **Jaeger** (traces) and **Prometheus → Grafana** (metrics), with a
prebuilt **SwarmKit Fleet** Grafana dashboard.

This is the design's "recommended default collector bundle" (doc
[14-aggregation.md](../../design/details/control-plane/14-aggregation.md) open question), and stays
BYO-friendly — swap any backend, or point instances at your existing collector instead.

## Run

```bash
docker compose -f deploy/observability/docker-compose.yml up -d
```

| Service | URL | Notes |
| --- | --- | --- |
| OTel Collector | `localhost:4317` (gRPC), `localhost:4318` (HTTP) | where instances send OTLP |
| Jaeger | http://localhost:16686 | traces UI |
| Grafana | http://localhost:3001 | dashboards (admin/admin; anonymous read enabled) |
| Prometheus | http://localhost:9090 | metrics store |

## Point an instance at it

SwarmKit's runtime already exports OTLP — just set the exporter + endpoint:

```bash
SWARMKIT_OTEL_EXPORTER=otlp \
SWARMKIT_OTEL_ENDPOINT=http://localhost:4318/v1/traces \
swarmkit serve
```

Give each instance a distinct `service.name` (config `telemetry.service_name`) so the dashboard's
**Instance** variable and Jaeger's service filter separate them.

## Publishing to multiple collectors / backends

A SwarmKit instance exports to a **single** OTLP endpoint (`SWARMKIT_OTEL_ENDPOINT`). To send the
same telemetry to several backends, let the **collector fan out** rather than the instance: add
another exporter in [`otel-collector-config.yaml`](otel-collector-config.yaml) and list it under the
relevant pipeline (a commented Honeycomb example is included). This is the standard OTel pattern —
one collector, many backends. (Native multi-endpoint export from the instance itself would be a
small runtime change — a second span processor — and isn't supported today.)

## Notes

- Metric names follow the OTel→Prometheus convention (`swarmkit.runs.total` → `swarmkit_runs_total`,
  etc.); adjust the dashboard queries if your collector version normalizes differently.
- Images are unpinned for first-run convenience — pin tags for reproducible deployments.
- Traces/metrics live here; the **control-plane panel** aggregates the SwarmKit-specific signals
  (audit/eval/usage rollups) separately and links out to these dashboards.
