# Plan: M6 — Observability + Human Interaction

## Context

M0-M5 are complete. M6 is the next milestone: add OpenTelemetry traces, a local ring buffer for prompt privacy, an AuditProvider abstraction (replacing JSONL files), governance circuit breakers, notification plugins, and rewrite CLI observability commands to use the new audit backend. This is the largest remaining milestone and makes SwarmKit production-ready for real workloads.

**Key decisions:**
- Ship all at once (not split into sub-milestones)
- Clean rewrite: CLI commands migrate from JSONL files to AuditProvider (SQLite default)
- JSONL export becomes an optional output format, not the primary store

## PR Ordering (12 PRs, 3 waves)

### Wave 1 — Foundation (parallelizable, no dependencies between them)

**PR 1: Audit event schema + skill schema extension**
- Expand `AuditEvent` dataclass in `governance/__init__.py` with full field set from `human-interaction-model.md`: `event_id`, `run_id`, `parent_event_id`, `agent_role`, `skill_category`, `inputs`, `outputs`, `verdict`, `reasoning`, `confidence`, `model_provider`, `model_name`, `tokens_in`, `tokens_out`, `cost_usd`, `duration_ms`, `policy_decision`, `policy_reason`, `error`
- Add workspace-scoped events: `RunStartedEvent`, `RunEndedEvent`, `HITLRequestedEvent`, `HITLResolvedEvent`
- Add `audit:` block to `skill.schema.json` (optional: `log_inputs`, `log_outputs`, `redact`)
- Redaction utility: `redact_json_pointers(obj, paths) -> dict`
- Per-category defaults (capability: summary/summary, decision: summary/full)
- Update codegen (Python + TS) for schema change
- Tests: redaction, category defaults, schema validation with audit block
- **Files:** `governance/__init__.py`, `packages/schema/schemas/skill.schema.json`, codegen outputs, `docs/notes/schema-change-discipline.md` checklist

**PR 2: AuditProvider ABC + SQLite implementation**
- `AuditProvider` ABC in new `audit/` module: `record()`, `query()`, `count()`
- `MockAuditProvider` — in-memory, for tests (replaces the events list on MockGovernanceProvider)
- `SQLiteAuditProvider` — local SQLite, default backend. Table: `audit_events` with all AuditEvent fields. Index on `run_id`, `agent_id`, `timestamp`.
- Config: `storage.audit: { provider: sqlite, config: { path: .swarmkit/audit.sqlite, retention_days: 365 } }`
- `build_audit_provider()` factory in `_workspace_runtime.py` (mirrors `build_governance()` pattern)
- Wire into `WorkspaceRuntime`: audit provider injected alongside governance provider
- `GovernanceProvider.record_event()` delegates to `AuditProvider.record()`
- Tests: record + query roundtrip, retention pruning, mock provider assertions
- **Files:** new `audit/__init__.py`, `audit/_sqlite.py`, `audit/_mock.py`, `_workspace_runtime.py`, `governance/__init__.py`

**PR 3: OpenTelemetry Phase 1 — traces + console exporter**
- Add deps: `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`
- `TelemetryConfig` dataclass: `enabled`, `exporter` (otlp/console/none), `endpoint`, `api_key`, `sample_rate`, `send_prompts`
- `SwarmKitTelemetry` class: `start_run()`, `start_agent_step()`, `record_tool_call()`, `record_governance_decision()`, `record_drift()`, `record_approval()`
- All spans use `swarmkit.*` semantic attributes
- Console exporter: human-readable spans to stderr
- OTLP/HTTP exporter: async batching to configured endpoint
- Default: `exporter: none` (opt-in)
- Config loaded from `~/.swarmkit/config.yaml` `telemetry:` block
- Inject into compiler: wrap agent node execution with spans, tool calls as child spans, governance as child spans
- Tests: span hierarchy (run → agent → tool), attribute presence, exporter config, `send_prompts` flag
- **Files:** new `telemetry/__init__.py`, `telemetry/_config.py`, `telemetry/_exporters.py`, `langgraph_compiler/_compiler.py` (instrumentation), `_workspace_runtime.py` (wiring), `pyproject.toml` (deps)

**PR 4: Local ring buffer**
- `PromptRingBuffer` class: SQLite-backed, keyed by span_id + run_id
- `store(span_id, run_id, agent_id, step, prompt, response, model)`
- `query_by_span_id()`, `query_by_run_id()`, `query_by_agent(last_n)`
- `prune_expired()` — TTL-based cleanup
- Default path: `.swarmkit/prompts.sqlite`, retention: 7 days
- Wire into compiler: after every LLM call, store prompt/response (if `send_prompts` is false, which is default — prompts stay local)
- Tests: store + query, TTL pruning, process restart survival
- **Files:** new `telemetry/_ring_buffer.py`, `langgraph_compiler/_compiler.py` (store calls), `_workspace_runtime.py` (init)

### Wave 2 — Features (after Wave 1)

**PR 5: Governance circuit breakers**
- Schema extension: `governance.limits` block in workspace.yaml
- Fields: `max_steps_per_agent`, `max_steps_per_run`, `max_runs_per_topology_per_day`, `max_cost_per_run_usd`
- Sensible defaults: `max_steps_per_run: 500`
- Enforcement in compiler: step counter per agent, checked before each agent node. Abort with clear error when exceeded.
- Cost tracking: accumulate `cost_usd` from model provider responses, check against limit
- Tests: breaker triggers at limit, clear error message, default values apply when not configured
- **Files:** `governance/__init__.py` (limits), `langgraph_compiler/_compiler.py` (enforcement), `packages/schema/schemas/workspace.schema.json`

**PR 6: Notification plugin system**
- `NotificationProvider` ABC: `notify(event: NotificationEvent)`
- `NotificationEvent` dataclass: `event_type`, `run_id`, `summary`, `metadata`
- Built-ins: `TerminalNotificationProvider`, `SlackNotificationProvider` (webhook), `EmailNotificationProvider` (SMTP), `WebhookNotificationProvider` (generic URL + template)
- `NotificationRegistry` + workspace config: `notifications:` array
- Fire on: `hitl_requested`, `run_ended { status: error }`, `skill_gap_surfaced`
- Wire into HITL flow (review queue) and run completion
- Tests: mock notification provider, webhook payload format
- **Files:** new `notifications/__init__.py`, `notifications/_providers.py`, `_workspace_runtime.py` (wiring), `packages/schema/schemas/workspace.schema.json`

**PR 7: OTel metrics**
- Counters: `swarmkit.runs.total`, `swarmkit.agent.steps.total`, `swarmkit.tool.calls.total`, `swarmkit.governance.decisions.total`
- Histograms: `swarmkit.runs.duration_ms`, `swarmkit.tool.duration_ms`, `swarmkit.approval.wait_ms`
- Wire into existing instrumentation points
- Tests: metric values after a topology run
- **Files:** `telemetry/__init__.py` (metrics setup), `langgraph_compiler/_compiler.py` (emit points)

### Wave 3 — CLI rewrite (after Wave 2)

**PR 8: CLI rewrite — status + logs + events**
- `swarmkit status` reads from AuditProvider (not JSONL files)
- `swarmkit logs <run-id> [--follow]` queries AuditProvider by run_id
- `swarmkit events [--follow] [--filter ...]` cross-run event stream
- Output: line-oriented JSON by default, `--pretty` for TTY
- Filters: `--agent`, `--skill`, `--category`, `--since`, `--until`
- Remove JSONL file reading logic, keep markdown export as a formatter
- Tests: CLI output against known audit data
- **Files:** `cli/__init__.py` (rewrite status/logs commands), remove `_save_run_log()` JSONL logic from `_workspace_runtime.py`

**PR 9: CLI rewrite — review + stop + why**
- `swarmkit review` reads from AuditProvider + ReviewQueue
- `swarmkit stop <run-id>` — graceful shutdown with checkpoint
- `swarmkit why <run-id>` — decision chain from AuditProvider (every decision-skill verdict in order)
- Tests: review approve/reject flow, why output
- **Files:** `cli/__init__.py`

**PR 10: CLI — debug subcommands**
- `swarmkit debug --span-id <id>` — query ring buffer
- `swarmkit debug --run-id <id>` — all prompts for a run
- `swarmkit debug --agent <name> --last <n>` — agent history
- Output: prompt + response pairs, model used, timestamp
- Tests: debug output with known ring buffer data
- **Files:** `cli/__init__.py`, integration with `PromptRingBuffer`

**PR 11: swarmkit ask — conversational observer rewrite**
- Rewrite to use AuditProvider queries instead of raw JSONL paste
- Scoped queries: `--run <run-id>`, default last 15 min
- Better context: structured event summary instead of raw dump
- Token cost footer
- Tests: mock model provider, verify context includes structured events
- **Files:** `cli/__init__.py`

**PR 12: Per-skill audit redaction + workspace audit.level**
- Wire redaction into the event emission path
- Compiler reads skill's `audit:` block, applies redaction before `record_event()`
- Workspace-level `audit.level` (minimal/standard/detailed) clamps per-skill settings
- Summary mode: first 200 bytes + field names/shapes
- Tests: redacted events don't contain full data, level clamping works
- **Files:** `langgraph_compiler/_compiler.py`, `langgraph_compiler/_skill_executor.py`, `_workspace_runtime.py`

## Design notes referenced

- `design/details/opentelemetry-observability.md` — OTel traces, metrics, attributes, exporters
- `design/details/human-interaction-model.md` — audit event schema, CLI primitives, notification plugins
- `design/details/product-architecture-refinements.md` — local ring buffer, circuit breakers
- `design/details/market-analysis-and-risk-mitigations.md` — circuit breakers (Risk 3)

## Key existing code to modify

- `packages/runtime/src/swarmkit_runtime/governance/__init__.py` — AuditEvent expansion, delegate to AuditProvider
- `packages/runtime/src/swarmkit_runtime/governance/_mock.py` — wire MockAuditProvider
- `packages/runtime/src/swarmkit_runtime/langgraph_compiler/_compiler.py` — OTel instrumentation, ring buffer stores, circuit breaker checks, redaction
- `packages/runtime/src/swarmkit_runtime/langgraph_compiler/_skill_executor.py` — redaction on skill audit blocks
- `packages/runtime/src/swarmkit_runtime/_workspace_runtime.py` — build_audit_provider(), telemetry init, ring buffer init, remove JSONL persistence
- `packages/runtime/src/swarmkit_runtime/cli/__init__.py` — rewrite all observability commands
- `packages/schema/schemas/skill.schema.json` — audit block extension
- `packages/schema/schemas/workspace.schema.json` — storage.audit, governance.limits, notifications, telemetry blocks
- `packages/runtime/pyproject.toml` — add opentelemetry deps

## New modules to create

- `packages/runtime/src/swarmkit_runtime/audit/__init__.py` — AuditProvider ABC
- `packages/runtime/src/swarmkit_runtime/audit/_sqlite.py` — SQLiteAuditProvider
- `packages/runtime/src/swarmkit_runtime/audit/_mock.py` — MockAuditProvider
- `packages/runtime/src/swarmkit_runtime/telemetry/__init__.py` — SwarmKitTelemetry
- `packages/runtime/src/swarmkit_runtime/telemetry/_config.py` — TelemetryConfig
- `packages/runtime/src/swarmkit_runtime/telemetry/_exporters.py` — console + OTLP exporters
- `packages/runtime/src/swarmkit_runtime/telemetry/_ring_buffer.py` — PromptRingBuffer
- `packages/runtime/src/swarmkit_runtime/notifications/__init__.py` — NotificationProvider ABC + built-ins

## Verification — MANDATORY for every PR

**No PR is approved without an e2e test result attached.**

For each PR:
1. `just test` passes (pytest + vitest)
2. `just lint` + `just typecheck` clean
3. `just schema-codegen-check` passes (for schema PRs)
4. **E2E test:** run a real topology (hello-swarm or reference/code-review) through the actual `swarmkit run` CLI to verify the changes work in a real execution path
5. **Attach the output:** paste the actual CLI output (terminal transcript) into the PR description as proof the feature works. Not unit test output — the real CLI run.

### Per-PR e2e verification details:

- **PR 1 (Audit event schema):** `swarmkit run` a topology → verify new AuditEvent fields populated in the returned events. Show the event JSON.
- **PR 2 (AuditProvider):** `swarmkit run` → verify events written to SQLite. Show `sqlite3 .swarmkit/audit.sqlite "SELECT * FROM audit_events LIMIT 5"` output.
- **PR 3 (OTel traces):** `swarmkit run` with `telemetry.exporter: console` → show span output in terminal.
- **PR 4 (Ring buffer):** `swarmkit run` → `swarmkit debug --run-id <id>` → show prompt/response retrieval.
- **PR 5 (Circuit breakers):** `swarmkit run` with `max_steps_per_run: 3` → show abort with clear error message.
- **PR 6 (Notifications):** `swarmkit run` triggering a notification → show webhook payload or terminal output.
- **PR 7 (OTel metrics):** `swarmkit run` → show metric values after completion.
- **PR 8 (CLI status/logs/events):** `swarmkit run` → `swarmkit status` + `swarmkit logs <run-id>` → show output from the new AuditProvider-backed commands.
- **PR 9 (CLI review/stop/why):** `swarmkit run` a topology with HITL → `swarmkit review` + `swarmkit why` → show output.
- **PR 10 (CLI debug):** `swarmkit run` → `swarmkit debug --span-id <id>` → show full prompt/response.
- **PR 11 (swarmkit ask):** `swarmkit run` → `swarmkit ask "what happened?"` → show LLM response with structured context.
- **PR 12 (Redaction):** `swarmkit run` with a skill that has `audit.redact` → show redacted event vs full event.
