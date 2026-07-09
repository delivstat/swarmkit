# 25 — fleet UI → Jaeger trace deep-link

Status: accepted. Brings the CLI's `swarmkit trace` story (the run waterfall) into the fleet UI,
now that runs emit OTel spans (runtime/otel-trace-export) and each instance is its own Jaeger
service (`service.name = workspace id`).

## Goal

The Runs page (design 24) shows per-run cost/status but no way to see a run's **trace waterfall** —
the agent/tool span timeline the CLI's `swarmkit trace` renders. This adds a **"View in Jaeger"**
deep-link so an operator jumps from a selected instance's runs straight to its traces.

## Shape (UI-only, no backend change)

- Panel `/config` already exposes `observability.jaeger_url`; the instance's OTel **service name is
  its workspace id**, which the panel already has in the cached `InstanceState` (`state.workspace_id`,
  synced via `/fleet/state`).
- `lib/jaeger.ts` `jaegerServiceUrl(base, service)` builds `…/search?service=<id>&lookback=1h&limit=20`
  (null when either is missing). `components/JaegerLink` renders the button, or nothing when Jaeger
  isn't configured / the service is unknown.
- The Runs page shows the link in its header **when a single instance is selected** (traces are
  per-instance) — using the cached `workspace_id`, falling back to the instance name.

## Non-goals / follow-up

- **Per-run exact trace link.** This lands on the instance's *service* search (pick the run by
  time/topology), not a specific `traceID` — the panel's `/jobs/history` doesn't carry the OTel
  trace id. Exact linking needs the trace id threaded through serve `/jobs/history` → `/runs` → UI.
- **`why` (LLM run explanation).** The CLI's `swarmkit why` analyses a run's audit log via an LLM;
  a federated UI equivalent (panel → instance → analysis) is a separate, larger feature.
- Grafana metrics panels — out of scope; the observability card already links Grafana.

## Test plan

- `jaegerServiceUrl`: builds/encodes the URL, trims trailing slash, returns null when unconfigured.
- `JaegerLink`: renders the service-scoped link (target `_blank`); renders nothing without a base
  URL or service.
