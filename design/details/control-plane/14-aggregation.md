# 14 — Phase 4: Aggregation (observability)

Builds on [06](06-observability-eval.md) (what each instance emits) and the architecture's D3
([11](11-architecture.md)). Designs how the panel aggregates across instances **without rebuilding
generic observability**.

## Goal

A fleet-wide view of runs, traces, evals, audit, usage — leaning on the OTel ecosystem for raw
telemetry and a small central store for the SwarmKit-specific, compliance-grade data.

## What goes where (D3)

| Signal | Transport | Store | Panel surface |
|---|---|---|---|
| Traces | OTLP push → **collector** | Tempo/Jaeger/Grafana/Honeycomb/… | link/embed (don't rebuild) |
| Metrics (`swarmkit.*`) | OTLP push → collector | Prometheus/OTel backend | link/embed dashboards |
| Audit | push to panel API (or heartbeat-batched) | **central Postgres** (append-only, merged, deduped by `event_id`, +`instance_id`) | fleet audit query (compliance) |
| Eval results | push to panel API | central Postgres | pass-rate trends + regressions |
| Usage / cost | push to panel API | central Postgres | rollups by model/provider/date |
| Current jobs | **federated live-query** (call serve) | none (live) | live status |
| Prompts | stays local (private) | not centralized | only if `send_prompts` |

## Ingestion

- **Raw traces/metrics:** each instance's `telemetry.endpoint` points at the **fleet collector**
  (config the panel sets during enroll). The panel does not handle raw spans.
- **Audit / eval / usage:** pushed to an authenticated **panel aggregation API**
  (`POST /aggregate/{audit|eval|usage}`) — or batched on the heartbeat. Append-only; deduped by the
  global `event_id` / `(instance, eval_set, ts)` keys ([11](11-architecture.md) §5).
- **Current jobs / live usage:** the panel queries the instance's serve on demand (federated), not
  stored — avoids a write-heavy central job log.

## SwarmKit-specific signal views (the panel's unique value)

What the OTel UI can't interpret, the panel builds: **eval** pass-rate trends + regressions
(per topology, cross-instance), **intent drift** per agent, **governance** decisions + approval
waits, **skill gaps** (feeds the growth loop, [17](17-growth-loop.md)), **compression** ROI.

## Constraints carried forward

- **Cost analytics is blocked** until ModelProviders populate `cost_usd` ([03](03-provider-seams.md),
  [06](06-observability-eval.md)) — the field/metric exist; the value doesn't. Prerequisite for cost
  dashboards.
- **Prompt privacy:** the local ring buffer is private by design; never centralize unless an
  instance explicitly sets `send_prompts`.
- **Audit is the compliance anchor** — it must be the durable, central, append-only store; traces
  may be sampled/retained by the collector independently.

## What Phase 4 builds

The panel aggregation API + central Postgres schema (audit/eval/usage); the instance-side push
(or heartbeat batch) for those three; collector wiring (config push during enroll); the panel's
SwarmKit-specific signal views; federated live-job query.

## Open questions

- Push vs pull for audit/eval/usage (recommend push to the panel API; pull-on-heartbeat as fallback
  for instances that prefer not to push).
- Retention split: central audit retention vs collector trace retention.
- Whether to ship a recommended collector bundle or stay BYO-collector (recommend BYO + a documented
  default).
