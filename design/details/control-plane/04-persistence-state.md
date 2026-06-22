# 04 — Persistence & state

Scope: everything a control plane reads, aggregates, or syncs. Almost all of it is **per-instance
SQLite + local JSON under `.swarmkit/`** — the central data-plane problem.

## Runtime store — `.swarmkit/store.sqlite` (`persistence/_sqlite.py`)

Backend resolution: `SWARMKIT_STORE_BACKEND` env → `storage.runtime.backend` → default `sqlite`
(`persistence/_factory.py`). Postgres is plumbed but **falls back to sqlite** (not implemented).

- **`jobs`** — id, topology, status, input, version, output, error, events(JSON), created/completed_at,
  usage_input_tokens, usage_output_tokens, usage_cost_usd. Indexes: status, created_at.
- **`conversations`** — id, topology, created/updated_at, turns(JSON), metadata(JSON).
- **`run_usage`** — id, job_id?, conversation_id?, agent_id, model, input/output/cache_read tokens,
  cost_usd, created_at. (Per-LLM-call metering; `cost_usd` mostly 0 until providers emit cost.)

## Run traces — `.swarmkit/traces/<run_id>.json` (`trace.py`)

`RunTrace`: run_id, topology, timing, total tokens, llm_calls, `agent_steps[]` (`AgentStep`:
agent_id/model/role/parent_agent, timing, tokens, `tool_calls[]`, delegations, forced_synthesis),
`token_by_agent`, `token_by_model`, and compression stats (`compression_bytes_in/out`,
`compression_calls`, `compression_by_backend`). `ToolCall`: tool_name, arguments, result_length,
error, duration_ms, cached. Saved as JSON; `render_text()` for `swarmkit trace`.

## Audit — `.swarmkit/audit.sqlite` (`audit/_sqlite.py`, schema = `AuditEvent`)

Append-only (`INSERT OR IGNORE`; no update/delete). `audit_events` columns: event_id(PK), event_type,
agent_id, timestamp, run_id, parent_event_id, topology_id, skill_id, agent_role, skill_category,
inputs, outputs (redactable), verdict/reasoning/confidence (decision skills), model_provider/name,
tokens_in/out, cost_usd, duration_ms, policy_decision/reason, error, payload. Indexes: run_id,
agent_id, timestamp, event_type. Retention `retention_days` (default 365) via `prune_expired()`.

## Other per-instance state

- **Checkpoints** — `.swarmkit/state/checkpoints.db` (LangGraph `SqliteSaver`, thread_id-keyed;
  backs `swarmkit run --resume`). `.swarmkit/state/last_thread.txt` = last resumable thread.
- **Run-state / scratch** — `.swarmkit/run-state/current/{scope.json,tasks.json}`; archived to
  `.swarmkit/run-state/<run_id>/` after a run.
- **Memory** — `.swarmkit/memory.json` (`memory/_store.py`, TF-IDF search; or GBrain MCP backend).
- **Notifications** — `.swarmkit/notifications.sqlite` (delivery history).
- **Conversations** — `.swarmkit/conversations/<id>.json`.
- **Prompt ring buffer** — `.swarmkit/prompts.sqlite` (local-only LLM prompt/response; see [06](06-observability-eval.md)).
- **Eval results** — `.swarmkit/eval-results/<id>-<ts>.json`.

## On-disk workspace artifacts

`workspace.yaml` (+ `workspace.env[.ENV].yaml` interpolation, `SWARMKIT_ENV`-selected) + dirs
`topologies/`, `archetypes/`, `skills/`, `triggers/`, `schedules/` (each `kind: …`). Max one level
of subdir nesting. Discovery (`workspace/__init__.py:126`) → resolution (`resolver/`).

## Control-plane implications

| State | Sync model for a fleet |
|---|---|
| jobs / run_usage | query + aggregate by created_at, topology, (model, provider, date); central store or pull |
| traces | object store (S3/GCS) + index by run_id; panel parses for graph/token/compression |
| audit | **central append-only store** (Postgres/AGT-style), merged + deduped by `event_id` — compliance-critical |
| checkpoints | per-instance; resume must route to the owning instance, or central checkpoint store for HA |
| memory | central K/V or vector store if shared; per-user partitioned |
| notifications | merge + dedup by event id |
| workspace artifacts | **central versioned registry** + push/pull sync; record active version per run (`jobs.version`) |

- The biggest design decision is **central store vs federated query**. Audit and artifacts argue
  for central (compliance + reproducibility); traces/usage can be federated query or OTel-piped.
- No artifact content-hash/version today (except topology canary versions) — a registry needs to
  add provenance + versioning ([07](07-schema.md), [10](10-capability-map.md)).
