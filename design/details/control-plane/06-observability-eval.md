# 06 — Observability & eval

Scope: `telemetry/` (OTel), `eval/` (M15), `drift/` (M7), prompt ring buffer, notifications.

## OpenTelemetry (`telemetry/_tracer.py`, `_metrics.py`, `_config.py`)

`SwarmKitTelemetry` facade; prefix `swarmkit.`. Config via env (`SWARMKIT_OTEL_EXPORTER`
`console|otlp|none`, `SWARMKIT_OTEL_ENDPOINT`, `SWARMKIT_OTEL_API_KEY`, `SWARMKIT_OTEL_HEADERS`) or
`~/.swarmkit/config.yaml` `telemetry:` block (`enabled`, `exporter`, `endpoint`, `api_key`,
`sample_rate`, `send_prompts`, `service_name`). OTLP via `BatchSpanProcessor`.

- **Spans:** `topology.run` (topology.id, run.id, workspace.id); `agent.step.<id>` (agent.id, step,
  archetype, role); `tool.call.<name>` (tool.name, tool.server) + `tool.status`/`error.type`;
  model usage attrs (provider, id, tokens_in/out, cost_usd).
- **Events:** `governance.decision` (decision, policy, scope); `intent.drift` (score, threshold,
  action, exceeded).
- **Metrics:** counters `swarmkit.runs.total`, `.agent.steps.total`, `.tool.calls.total`,
  `.governance.decisions.total`, `.agent.drift.breaches.total`, `.compression.bytes_saved.total`;
  histograms `.runs.duration_ms`, `.tool.duration_ms`, `.approval.wait_ms`, `.agent.drift.score`,
  `.compression.ratio`.

## Run trace + UsageSummary

`RunTrace` (see [04](04-persistence-state.md)) → CLI `swarmkit trace` (`render_text`), `swarmkit
status`, `/usage`. `UsageSummary` (`_workspace_runtime.py`): tokens by agent/model + compression
stats; surfaced in `RunResult.usage` and the CLI run summary.

## Eval harness (M15)

`eval/`: `EvalSet{target, cases[EvalCase{input, ExpectSpec}]}`. `ExpectSpec`: `contains`,
`not_contains`, `regex`, `equals`, `not_empty`, `used_skills` (trajectory), `judge` (decision-skill
id), `rubric` (inline), `min_confidence`. `run_eval_set` runs each case, deterministic checks +
trajectory (`RunEvent.skill_id`) + LLM judge/rubric (`runtime.judge`/`judge_rubric`). CLI
`swarmkit eval <ws> <eval-set> [--compare] [--output] [--quiet]`; reports →
`.swarmkit/eval-results/<id>-<ts>.json`; `--compare` diffs vs latest prior (regressed/fixed/new).
Exit 1 on any fail (CI-gateable). Eval-sets live under `evals/`.

## Intent drift (M7) — `drift/`

`IntentMonitoringConfig` (topology `intent_monitoring:`): `enabled`, `threshold` (default 0.75),
`on_drift` (`log|warn|nudge`). `IntentObserver.observe()` = `1 - cosine(anchor, output)` via
sentence-transformers (TF-IDF fallback). Emits drift metrics + span events.

## Prompt ring buffer — `.swarmkit/prompts.sqlite` (`telemetry/_ring_buffer.py`)

Local-only prompt/response capture keyed by OTel span_id (never sent to the backend unless
`telemetry.send_prompts`). `swarmkit debug` queries by span/run/agent. Retention 7 days default.

## Notifications

`NotificationProvider.notify(NotificationEvent)`; events `hitl_requested`, `run_ended_error`,
`skill_gap_surfaced`; channels Terminal/Webhook/Slack/Discord/Telegram; delivery history in
`.swarmkit/notifications.sqlite`.

## Control-plane implications

- **Aggregate via the OTel ecosystem — do not rebuild trace UI.** Instances export OTLP to a
  collector (Tempo/Jaeger/Grafana/Honeycomb/Datadog); the panel links out or embeds. This is the
  cheapest correct path and avoids competing with mature tools.
- **The panel's unique value = SwarmKit-specific signals** the OTel UI can't interpret: eval pass-
  rate trends + regressions (from `eval-results/`), intent-drift per agent, governance decisions +
  approval waits, compression ROI, and skill-gap surfacing (the growth loop).
- **Cost analytics is blocked** until ModelProviders populate `cost_usd` (the metric/field exists).
  Flag as a dependency for fleet cost dashboards.
- Prompt ring buffer is deliberately local/private — the panel should respect that and not
  centralize prompts unless `send_prompts` is explicitly enabled.
