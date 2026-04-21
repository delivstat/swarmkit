---
title: Human interaction and observability model (v1.0)
description: How a human starts a swarm, observes it, and intervenes mid-run. Structured audit log + CLI primitives + a conversational observer — all v1.0.
tags: [runtime, observability, cli, hitl, v1.0]
status: in-review
---

# Human interaction and observability model

**Scope:** runtime, CLI, audit log, review queue, notification layer.
**Design reference:** §3.4 (first-run promise), §5.4 (review queues + triggers), §14.1 (execution modes), §14.2 (authoring entry points), §15 (UI surfaces — v1.1), §16.4 (audit logging).
**Status:** in review — decisions here gate M2, M4, and parts of M5.

## Goal

Specify the end-to-end experience of a human working with a running SwarmKit swarm: how they start it, monitor it, talk to it mid-run, and interact when the swarm pauses for approval. Close the gap that v0.6 leaves between authoring (well-specified, §14.2) and operation (under-specified).

## Non-goals

- **The v1.1 UI.** This note is about the CLI + plugin surface that must exist before (and regardless of) any UI. The UI consumes the same APIs the CLI consumes.
- **Execution-engine internals.** How the LangGraph compiler dispatches is M3's concern; this note specifies the *observation surface* over whatever the runtime does.
- **Multi-tenant isolation.** v1.0 is single-tenant per workspace; isolation is a v2.0 hardening target.

## Design principle

**The runtime is observable or it isn't real.** A runtime path that doesn't emit structured events cannot be monitored, asked about, audited, or reproduced. Every agent invocation, every skill call, every policy decision, and every HITL interaction writes a structured event before the next step happens. Observability is a first-class design constraint, not a feature layered on top.

## Three layers of observability

Each layer has a distinct purpose and a distinct consumer.

### Layer 1 — Structured audit log (data source)

**Who knows about storage.** Skills do not. They emit events via `GovernanceProvider.record_event(event)` — one path, one contract. The governance provider delegates to a **pluggable `AuditProvider`** configured at the workspace level (see the "Storage pluggability" section below). Swapping backends — from the default SQLite to Postgres to AGT Agent SRE to a custom plugin — never touches skill code.

SwarmKit pins the event schema so skill authors don't invent their own log shapes. Every skill invocation emits one event:

| Field | Type | Purpose |
|---|---|---|
| `event_id` | UUID | Unique per event |
| `timestamp` | ISO-8601 + monotonic ns | Wall-clock + ordering guarantee |
| `run_id` | UUID | Which swarm run this belongs to |
| `parent_event_id` | UUID \| null | For tracing across handoffs |
| `agent_id` | string | Which agent invoked this |
| `agent_role` | root \| leader \| worker | |
| `parent_context` | list[string] | Agent ancestry trace |
| `skill_id` | string | Which skill ran |
| `skill_category` | capability \| decision \| coordination \| persistence | |
| `inputs` | object \| null | Redacted per policy (see below) |
| `outputs` | object \| null | Redacted per policy |
| `verdict` | "pass" \| "fail" \| "needs-review" \| null | Decision skills only |
| `reasoning` | string \| null | Decision skills only (full, per design §6.3 contract) |
| `confidence` | number \| null | Decision skills only |
| `model_provider` | string \| null | Via ModelProvider registry |
| `model_name` | string \| null | |
| `tokens_in` | int \| null | |
| `tokens_out` | int \| null | |
| `cost_usd` | number \| null | Computed by model provider at emit time |
| `duration_ms` | int | |
| `policy_decision` | "allow" \| "deny" | From `GovernanceProvider.evaluate_action` |
| `policy_reason` | string \| null | If denied |
| `error` | object \| null | `{ type, message, traceback_hash }` |

Plus three **workspace-scoped** event kinds (not per-skill):

- `run_started { run_id, topology_id, trigger_source, inputs }`
- `run_ended { run_id, status, total_cost_usd, duration_ms }`
- `hitl_requested { run_id, review_queue_id, summary }` / `hitl_resolved { ..., decision, by_user }`

Events are emitted via `GovernanceProvider.record_event` — a single path, append-only semantics enforced at the storage layer. No code path in the runtime may update or delete an event.

### Storage pluggability — `AuditProvider`

Where events actually land is a **workspace-level choice**, not a runtime-internal detail. Matches the established pattern (`ModelProvider`, `SecretsProvider`): narrow ABC, several built-in implementations, plugin path for custom.

```
┌─────────────┐
│   Skill     │   emits via GovernanceProvider.record_event(event)
└──────┬──────┘
       │  knows nothing about storage
       ▼
┌──────────────────────┐
│  GovernanceProvider  │   single facade for evaluate_action /
│  (e.g. AGT impl)     │   verify_identity / record_event /
└──────┬───────────────┘   get_trust_score
       │  delegates persistence to the configured AuditProvider
       ▼
┌──────────────────────┐
│    AuditProvider     │   mock | sqlite | postgres | agt | plugin
└──────────────────────┘
```

**Interface (illustrative):**

```python
class AuditProvider(ABC):
    provider_id: ClassVar[str]

    @abstractmethod
    async def record(self, event: AuditEvent) -> None:
        """Append-only. No update, no delete — ever."""

    @abstractmethod
    async def query(
        self,
        *,
        run_id: str | None = None,
        since: datetime | None = None,
        filters: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[AuditEvent]:
        """Read-only query used by CLI primitives, `swarmkit ask`, and
        notification consumers."""

    @abstractmethod
    async def count(self, filters: Mapping[str, Any] | None = None) -> int:
        """For dashboards / quick status queries."""
```

**v1.0 built-ins:**

| `provider_id` | Backing | Use case |
|---|---|---|
| `mock` | in-memory | tests |
| `sqlite` | local SQLite file | default; dev + single-node prod |
| `postgres` | Postgres | multi-node prod, shared query |
| `agt` | AGT Agent SRE | compliance-heavy deployments |
| `plugin` | entry-point-discovered | S3, OpenSearch, Datadog, Splunk, custom |

**`sqlite` is default.** Zero-config path — a user running `swarmkit serve` gets a working audit log at `.swarmkit/audit.sqlite` without touching workspace.yaml.

**Workspace config** — extends the existing `storage.audit` block to the uniform `{ provider, provider_id?, config }` shape (matches `SecretsProvider`):

```yaml
storage:
  audit:
    provider: sqlite                        # built-in
    config:
      path: ./.swarmkit/audit.sqlite
      retention_days: 365

# — or —
storage:
  audit:
    provider: postgres
    config:
      url_ref: audit_db_url                 # credentials_ref — never literal
      schema: swarmkit_audit
      retention_days: 365
      pool_size: 10

# — or — org-internal plugin
storage:
  audit:
    provider: plugin
    provider_id: acme-opensearch
    config:
      endpoint: https://search.acme.internal
      index: swarmkit-audit
```

**Skill-side invariant.** Skills call `governance.record_event(event)`. They never import an audit module. They never instantiate an `AuditProvider`. They never know whether events land in SQLite, Postgres, AGT's Agent SRE, or a plugin. This keeps the Separation of Powers clean (§8) and makes storage swaps safe.

**Why v1.0 ships all three (not just SQLite).** A serious workspace with scheduled topologies hits SQLite's single-writer ceiling quickly, and many teams already run Postgres. Shipping `postgres` from v1.0 is cheap given the abstraction. AGT remains the compliance-heavy path. Custom backends (S3, OpenSearch, Datadog) via `plugin`.

**Full spec lives in `design/details/audit-provider.md`** (task #40) — per-backend config shapes, retention semantics, query-filter vocabulary, plugin entry-point contract. That note lands before task #38 implementation. This section fixes the interface shape so downstream code can compile against it.

### Layer 2 — CLI primitives (scriptable, no LLM)

Read the audit log + review queue directly. Fast, shell-pipeable, no token cost. The `kubectl`-shaped surface for developers and CI.

| Command | Purpose |
|---|---|
| `swarmkit status` | Snapshot: running topologies, pending HITL items, last N runs with status |
| `swarmkit logs <run-id> [--follow]` | Tail structured events for one run (JSON by default, `--pretty` for humans) |
| `swarmkit events [--follow] [--filter ...]` | Cross-run event stream (filters: `--agent`, `--skill`, `--category`, `--since`, `--until`) |
| `swarmkit review` | Interactive TUI: list pending HITL items, approve / reject / edit each |
| `swarmkit stop <run-id>` | Graceful shutdown — sends a stop signal the runtime checkpoints against |
| `swarmkit why <run-id>` | Decision chain — every decision-skill verdict in the run, in order, with reasoning |

**Output contract:** every command emits line-oriented JSON by default (one event per line, shell-friendly). `--pretty` (or detected TTY without `--json`) switches to a human-formatted variant. This matches `kubectl`, `gh`, `heroku` conventions.

**No LLM, no network (beyond the audit store), no token cost.** A user on a box with no internet or no LLM credentials can still monitor.

### Layer 3 — `swarmkit ask "..."` (conversational observer)

One command, natural-language questions, LLM-backed. For "why did X happen" rather than "is X running."

```
$ swarmkit ask "why did the review swarm take 20 minutes?"
The run took 19m42s. 17m of that was a single invocation of
`code-quality-review` against `rynko-flow` MCP — the call timed out
once and retried with exponential backoff (run_id r-..., events 34-41).
Recommend raising max_latency_ms on that skill or adding a retry budget.
```

**Minimum viable implementation** — not a full topology. One agent, one call:

1. Parse the question.
2. Load recent audit events (default last 15 minutes, or a named run via `--run <run-id>`).
3. Load workspace state (loaded topologies, current runs, review queue summary).
4. Bundle as context + user question.
5. Send to the configured `ModelProvider` (from `workspace.yaml`).
6. Print the answer.

Grows into a proper swarm (with tool-calls for deeper queries, cross-run correlation, etc.) only if the single-shot pattern proves insufficient.

**Cost / token awareness.** `swarmkit ask` is not free — prints the token count + estimated cost at the bottom of each answer. The user sees what they're spending.

**Works with any provider.** Uses `ModelProvider` — so `OLLAMA_HOST=...` with a local model works as well as a cloud model.

## Redaction — governance-enforced, not optional

Logging every skill's inputs and outputs verbatim creates two problems:

1. **Noise.** A 100-step run with full I/O generates megabytes. `swarmkit ask` chokes on too much context. Humans skimming `swarmkit logs` drown.
2. **Privacy / compliance.** Skills that handle customer data (invoices, code diffs, credentials, PII) would log that data into the audit store. For EU-AI-Act, HIPAA, SOC2 — potential breach.

### Per-skill `audit:` block

Every skill schema grows a new optional block:

```yaml
audit:
  log_inputs: full | summary | none    # default: per-category (see below)
  log_outputs: full | summary | none   # default: per-category
  redact:                              # JSON-pointer paths stripped before emit
    - /invoice/customer_name
    - /invoice/email
```

**Per-category defaults:**

| Category | Default log_inputs | Default log_outputs |
|---|---|---|
| capability | summary | summary |
| decision | summary | **full** (verdict + reasoning are audit-critical) |
| coordination | summary | summary |
| persistence | summary | none (the write is the event) |

`summary` semantics: the first 200 bytes of each top-level field, plus field names and shapes. Detailed enough to diagnose, small enough to scan.

### Workspace `audit.level`

Already in the workspace schema: `minimal | standard | detailed`. This becomes a governance-wide override:

- `minimal` — drops skill-level `full` to `summary`, drops `summary` to `none`. For prod compliance.
- `standard` — honours per-skill `audit:` block (the default).
- `detailed` — promotes all skill-level `summary` to `full`. For local debugging only; policy-enforced off in prod.

## Notification plugin

HITL review items need to reach the human. Without a push, the user would have to poll the review queue.

**Plugin shape** — mirror of ModelProvider / SecretsProvider:

```yaml
# workspace.yaml
notifications:
  - source: terminal        # built-in: print to the serve process stdout
  - source: slack
    config:
      webhook_ref: slack_webhook_secret  # credentials_ref
      channel: "#swarmkit-reviews"
  - source: email
    config:
      smtp_host: smtp.acme.internal
      to: ops@acme.com
      from_ref: email_from_secret
  - source: plugin
    provider_id: pagerduty
    config: { routing_key_ref: pd_routing_key }
```

**v1.0 built-in sources:** `terminal`, `stdout`, `slack` (webhook), `email` (SMTP), `plugin`. PagerDuty / Opsgenie via `plugin`; may promote to built-in based on demand.

Notifications fire on three events: `hitl_requested`, `run_ended { status: error }`, and `skill_gap_surfaced` (§12.1). Each notification provider's `config` can scope which events it cares about.

## Starting a swarm

Unchanged from §14.1 — three modes:

- **One-shot:** `swarmkit run <topology> [--input ...]` — CLI execution. Streams events to stdout.
- **Persistent:** `swarmkit serve <workspace>` — long-running process, exposes HTTP endpoints registered by triggers, accepts manual triggers via `swarmkit trigger fire <trigger-id>`.
- **Scheduled:** `swarmkit serve` (same as persistent — cron/webhook/file_watch triggers run automatically).

`swarmkit serve` runs in foreground by default (logs to stdout). `swarmkit serve --daemon` writes to `.swarmkit/logs/` and detaches. CLI observability commands (`status`, `logs`, `events`) work across both.

## Worked example

A user runs the Code Review Swarm. Mid-run an LLM judge flags a PR as low-confidence, triggering HITL. From the user's seat:

```
$ swarmkit run code-review-swarm.yaml --input @pr-diff.json
run_id: r-2026-04-21-14-02-a3b1
[14:02:11] engineering-leader → code-reviewer: assigning
[14:02:18] code-reviewer: running skill 'code-quality-review'
[14:02:35] code-reviewer: verdict=needs-review confidence=0.62 → review queue
[14:02:35] hitl requested: see `swarmkit review r-2026-04-21-14-02-a3b1`

# in another terminal
$ swarmkit review r-2026-04-21-14-02-a3b1
pending review — code-reviewer verdict on PR #1234
  reasoning: "Imports suggest a circular dependency but the linter
  didn't flag. Unclear without repo context."
[a]pprove  [r]eject  [e]dit  [s]kip
> a
approved — run resumed

# user also curious about what's been expensive
$ swarmkit ask --run r-2026-04-21-14-02-a3b1 "what's costing the most?"
Of 14 skill invocations, `code-quality-review` cost the most at
$0.12 / 8,400 tokens, run against claude-sonnet-4-6. Next biggest:
`security-specific-review` at $0.07. Total run cost so far: $0.24.
— 1,200 tokens / $0.003
```

Every line of that comes from the structured audit log. The `ask` path uses the ModelProvider. The `review` path reads the queue. Nothing is bespoke to this topology.

## API shape — summary

```python
# In the runtime
from swarmkit_runtime.observability import (
    AuditEvent,         # the pinned schema, frozen dataclass
    emit_event,         # writes via GovernanceProvider.record_event
)

# In the CLI
from swarmkit_runtime.cli.observability import (
    status_command,
    logs_command,
    events_command,
    review_command,
    stop_command,
    why_command,
    ask_command,
)
```

## Test plan (sketched — belongs to the implementation PRs)

- Unit: every field in `AuditEvent` is populated by the emit path; redaction strips the listed JSON pointers.
- Integration: a `MockGovernanceProvider` captures events; run a test topology and assert the emitted events match the expected trace.
- CLI: fixture workspaces with pre-populated audit logs; run each command, assert JSON / pretty output matches snapshots.
- `swarmkit ask`: uses `MockModelProvider` with a scripted response; asserts the event context is assembled correctly and token budgeting is reported.

## Follow-ups (separate PRs, tracked as tasks)

- **Task #34** — structured audit event schema implementation (ties into `GovernanceProvider` wiring; M2). **Includes adding the `audit:` block to `skill.schema.json` as a full schema-change-discipline PR** — update the schema file, add fixtures (one per category exercising the defaults + one with explicit redact paths), regenerate pydantic + TS types, extend `design/details/skill-schema-v1.md`, run `just demo-skill-schema`.
- **Task #38** — `AuditProvider` ABC + `sqlite` + `postgres` + `agt` + plugin built-ins + registry (M2, paired with #34).
- **Task #39** — workspace schema update: `storage.audit.backend` enum → uniform `{ provider, provider_id?, config }` shape. Full schema-change-discipline flow. Matches `SecretsProvider` pattern.
- **Task #40** — `design/details/audit-provider.md` — detailed per-backend config shapes, retention semantics, query-filter vocabulary, plugin entry-point contract. Blocks task #38 implementation.
- **Task #35** — CLI primitives implementation (M4).
- **Task #36** — `swarmkit ask` implementation (M4 or M5 once ModelProvider tool-calling is ready).
- **Task #37** — notification plugin shape + v1.0 built-ins (M4).
- Discipline note: `docs/notes/observability.md` (ships with this PR) — per-PR reminder that new runtime paths emit events, new skills declare their audit block.

## Open questions

- **Retention.** How long are audit events kept? Per-tier (minimal / standard / detailed) retention? Not decided; default `retention_days: 365` on workspace.yaml, user-overridable.
- **Event schema evolution.** When the schema adds a field in v1.1, old events in storage don't have it. Readers tolerate missing fields (non-breaking) — additive only within v1.
- **`swarmkit ask` privacy.** Does `ask` send redacted events or full events to the LLM? **Redacted only.** Otherwise redaction at the log layer is defeated by the observer. Document this explicitly.
- **Cost budget enforcement.** Does `swarmkit serve` have a token / dollar budget it enforces? Out of scope for this note; §8.6 governance-layer concern.
