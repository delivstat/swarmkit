---
title: Distributed architecture — Postgres backends, conversation persistence, Supabase unification
description: Design for scaling swarmkit serve beyond single-process. Shared Postgres for checkpoints, jobs, audit. Conversation resume across restarts. Supabase as unified backend.
tags: [serve, distributed, persistence, supabase]
status: design
---

# Distributed architecture

## Problem

`swarmkit serve` today is single-process: in-memory job store, SQLite
checkpointer, local audit log. This works for development and low-traffic
deployments but has three limitations:

1. **Server restart loses job state** — in-memory `JobStore` is gone.
2. **Conversations can't survive restarts** — SQLite checkpointer is local
   to the process. Restart = lose all conversation history.
3. **Can't scale horizontally** — two `swarmkit serve` instances can't
   share state. No load balancing across workers.

## Design

### Three-layer architecture

```
┌─────────────────────────────────────────────────┐
│  Layer 1: API Gateway (stateless)                │
│  swarmkit serve instances behind load balancer    │
│  Auth, rate limiting, canary routing              │
│  Dispatches jobs to queue, doesn't execute them   │
└──────────────────────┬──────────────────────────┘
                       │ job queue
                       ▼
┌─────────────────────────────────────────────────┐
│  Layer 2: Worker Pool (stateful per-job)          │
│  Each worker pulls one job from the queue          │
│  Loads workspace, compiles topology, runs agents   │
│  Checkpoints to shared Postgres                    │
│  Horizontally scalable                             │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  Layer 3: Shared Infrastructure                   │
│  Postgres: checkpoints, jobs, audit, conversations │
│  Redis (optional): job queue, SSE pubsub           │
│  Object store: task results, traces                │
│  OTel collector: centralized telemetry             │
└─────────────────────────────────────────────────┘
```

### Incremental path (no big-bang rewrite)

**Step 1: Postgres backends (single process)**

Swap SQLite for Postgres in three places. Single `swarmkit serve` process,
but all state is durable and survives restarts.

- `PostgresSaver` for LangGraph checkpointing (already exists in langgraph)
- `PostgresAuditProvider` for audit log (`AuditProvider` ABC already defined)
- `PostgresJobStore` for job state (replace in-memory dict)
- `PostgresConversationStore` for conversation → thread_id mapping

Config via `workspace.yaml`:

```yaml
storage:
  backend: postgres
  url: "${DATABASE_URL}"
```

Or via `workspace.env.yaml`:

```yaml
database_url: "env:DATABASE_URL"
```

**Step 2: Job queue (multi-worker)**

Extract job dispatch. Gateway enqueues, workers dequeue.

- Postgres `LISTEN/NOTIFY` for simple queue (no Redis needed at low scale)
- Redis or NATS for high-throughput queue
- SSE streaming via Redis pubsub (worker publishes events, gateway subscribes)

**Step 3: MCP gateway (shared tool servers)**

Single process wrapping all workspace MCP servers. Workers call tools
via HTTP instead of spawning child processes. Already designed in
`design/details/mcp-discovery-pattern.md`.

### What stays the same at every step

- `WorkspaceRuntime.run()` API — unchanged
- Topology YAML — no distributed-specific config
- Governance — each worker has its own `GovernanceProvider`
- Compiler — runs per-worker
- Ring buffer — stays local per-worker (prompts never leave the node)

## Conversation persistence

### How it works today

CLI chat mode:
1. `swarmkit chat` creates a conversation with a unique `thread_id`
2. Each turn calls `WorkspaceRuntime.run()` with that `thread_id`
3. LangGraph's `SqliteSaver` checkpoints the full graph state after each turn
4. `--resume` loads the checkpoint and continues

The checkpointed state includes: full message history, agent delegation
state, tool call results, task plans, scope decisions — everything.

### How it works with Postgres

Identical flow, but `PostgresSaver` replaces `SqliteSaver`. The
checkpoint is in a shared database instead of a local file.

```
Turn 1: Worker 3 handles it → saves to Postgres (thread_id=abc123)
Turn 2: Worker 1 picks it up → loads from Postgres → continues
Turn N: Any worker, any time, even weeks later → loads → continues
```

### Conversation resume across restarts

Conversations are indefinitely resumable. The full state is in Postgres.
A server can restart, a new deployment can roll out, and users resume
their conversations without losing context.

For the HTTP API:

```
POST /conversations                        # create new
POST /conversations/{id}/messages          # send message (any worker)
GET  /conversations                        # list all (from Postgres)
GET  /conversations/{id}                   # get with full history
```

### Long conversation management

Model context windows are finite. A 200-turn conversation exceeds any
model's context. Two strategies:

**Message trimming** — LangGraph supports keeping only the last N messages
in the prompt. Older messages are in the checkpoint but not sent to the
model. The agent loses access to old turns unless a search tool is provided.

**Compaction** — periodically summarize older turns into a condensed
system message. Same pattern Claude Code uses ("this session is being
continued from a previous conversation"). Full history in Postgres,
condensed version in the prompt.

Both can be configured per-topology:

```yaml
runtime:
  mode: persistent
  conversation:
    max_context_messages: 50
    compaction: summarize      # or: trim, none
```

## Supabase as unified backend

For deployments using Supabase (e.g., vedanta-advisor with GBrain),
all data lives in one Postgres instance:

```
Supabase Postgres
├── gbrain schema
│   ├── pages              # knowledge graph nodes
│   ├── edges              # relationships
│   └── embeddings         # pgvector search
├── langgraph schema
│   ├── checkpoints        # conversation state per turn
│   └── checkpoint_writes  # pending writes
├── swarmkit schema
│   ├── jobs               # job state
│   ├── conversations      # conversation_id → thread_id
│   └── audit_events       # audit log
```

One connection string. Three concerns separated by schema.

Supabase additionally provides:
- **pgvector** — GBrain embedding search
- **Row-level security** — multi-tenant isolation (future)
- **Realtime subscriptions** — could replace Redis pubsub for SSE
- **Edge Functions** — webhook trigger handlers (optional)
- **Auth** — Supabase Auth as a `JWTAuthProvider` source

### Workspace config for Supabase

```yaml
# workspace.env.yaml
supabase_url: "env:SUPABASE_URL"
supabase_key: "env:SUPABASE_SERVICE_KEY"
database_url: "env:DATABASE_URL"

# workspace.yaml
storage:
  backend: postgres
  url: "${database_url}"

mcp_servers:
  - id: gbrain
    transport: stdio
    command: ["npx", "gbrain-mcp"]
    env:
      DATABASE_URL: "${database_url}"
```

## What NOT to distribute

- **No agent-level distribution.** One topology = one worker. Splitting
  agents across nodes serializes state between them and destroys latency.
- **No custom orchestrator.** Use Kubernetes for worker scheduling.
  SwarmKit orchestrates agents, not infrastructure.
- **No distributed consensus.** Canary promotion, governance decisions
  are fine as eventually-consistent. No Raft/Paxos.
- **No cross-topology communication.** Mesh discovery is a governance
  liability, not a feature (per market-analysis design note).

## Implementation priority

For vedanta-advisor production deployment, only Step 1 is needed:

1. `PostgresSaver` checkpointer (conversation persistence)
2. `PostgresJobStore` (job state survives restarts)
3. `PostgresConversationStore` (conversation listing from DB)
4. Supabase connection wiring

Single `swarmkit serve` process + Supabase. No Redis, no queue, no
gateway. This handles the realistic load (tens of concurrent users,
not thousands) with full durability.

Steps 2-3 are future work for high-scale or multi-tenant deployments.

## Open questions

1. **Connection pooling** — Supabase has a built-in PgBouncer. Should
   SwarmKit use its own pool (asyncpg) or rely on Supabase's?
2. **Conversation cleanup** — TTL on old conversations? User-initiated
   delete? Or keep everything forever?
3. **Checkpoint size** — large conversations with many tool results can
   produce large checkpoints. Compress? Prune tool results after N turns?
4. **Migration path** — existing SQLite checkpoints and audit logs. One-time
   migration script or start fresh?
