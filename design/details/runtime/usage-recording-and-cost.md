# Usage recording + provider cost capture

Status: accepted. Fixes a dead write-path: runs execute but `run_usage` is never written, so
`/usage` and `/jobs/history` always report 0 tokens / $0 — the whole cost story is inert.

## The gap

The store has `record_usage(UsageRow)`, a `run_usage` table, and `/usage` + `/jobs/history`
endpoints that read it — but **`store.record_usage` is never called anywhere in the runtime**. The
compiler captures `response.usage` (input/output tokens) after every model call, but only into the
**in-memory `RunTrace`** (`record_llm_call` / `add_step`) for tracing. Nothing bridges that to the
store. Separately, cost is never computed at all — `Usage` carries only tokens.

## PR 1 (this note) — wire the write-path + capture OpenRouter's native cost

1. **`Usage` gains `cost_usd`.** OpenRouter returns per-call cost when the request asks for it
   (`extra_body={"usage": {"include": true}}`); `_from_openai_response` reads `raw.usage.cost`. Only
   the OpenRouter provider sets the request flag (`provider_id == "openrouter"`); other OpenAI-compat
   providers are unaffected and simply report `cost_usd = 0.0` (PR 2 adds a price table for them).
2. **Cost flows through the trace.** `record_llm_call` / `AgentStep` / `add_step` thread `cost_usd`;
   `RunTrace` accumulates `total_cost_usd` and a per-model `cost`. `UsageSummary` (on `RunResult`)
   exposes `cost_usd` + per-model cost.
3. **The write-path is wired.** `execute_job` (server), which already has the `store` + `job_id` +
   the `RunResult`, writes one `UsageRow` per model (`by_model`) at job completion — tokens + cost,
   keyed by `job_id`. So `/usage`, `/usage/{job_id}`, and `/jobs/history` report real numbers, and
   the fleet panel's Runs page (federated `/jobs/history`, design 24) shows real per-run cost.

## PR 2 — price table (shipped)

Providers that return tokens but no cost (Anthropic, OpenAI, Google; Ollama is local → $0) get
`cost_usd` derived from a per-model price table at record time. `model_providers/_pricing.py` holds
USD-per-1M `(input, output)` prices keyed by a normalized model prefix (aggregator prefix stripped,
lowercased, longest-key-wins so `gpt-4o-mini` isn't billed as `gpt-4o`); dated/variant suffixes
(`-20250514`, `-2024-08-06`) still resolve. `_record_run_usage` fills cost from the table **only
when the provider reported none** — OpenRouter's returned cost stays authoritative. An unpriced
model contributes `$0` (unknown, not guessed). Prices are approximate public list prices and need
maintaining when providers change them.

## Non-goals / follow-up

- No change to the trace file format beyond additive fields (back-compatible `asdict`/load).
- The in-memory trace's `total_cost_usd` is populated from provider-returned cost only; the price
  table applies at the persistence chokepoint (the authoritative `/usage` + `/jobs/history` surface).

## Test plan

- `_from_openai_response` reads `raw.usage.cost` into `Usage.cost_usd`; missing → 0.0.
- OpenRouter provider injects the usage-include flag; base OpenAI provider does not.
- `RunTrace` accumulates `total_cost_usd` + per-model cost across calls.
- `execute_job` writes one `UsageRow` per model with tokens + cost; `get_usage_summary(job_id)` and
  `get_usage_by_model` then return them.

## Demo

Run a topology on a real serve using an OpenRouter model → `GET /usage` and `/jobs/history` show
real tokens + `usage_cost_usd`; the panel Runs page renders the per-run cost.
