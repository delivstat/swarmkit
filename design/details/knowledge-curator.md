---
title: Knowledge Curator — domain knowledge for agent swarms
description: Dedicated topology that ingests, indexes, validates, and maintains a knowledge base. Workers read-only, curator writes. PII/secrets governance at ingest + query time.
tags: [knowledge, rag, curator, m5]
status: proposed
---

# Knowledge Curator

## Goal

Agents need domain knowledge to be useful in confined environments.
A code review swarm that doesn't know your codebase gives generic
advice. One that understands your architecture, conventions, and past
decisions gives actionable advice.

The Knowledge Curator is a **dedicated topology** whose only job is
maintaining the knowledge base. Worker topologies read from the KB
but never write to it. This separation prevents stale knowledge —
only the curator writes, and it validates freshness on a schedule.

## Why a separate curator, not self-building agents

An agent that writes to its own knowledge base creates a stale
knowledge risk:

```
Agent reviews code → discovers pattern → writes to KB
  6 months later...
Code refactored → pattern no longer exists → KB still says it does
Agent reads stale KB → gives wrong advice with false confidence
```

The agent doesn't know its knowledge is stale. It treats everything
in the KB as current fact.

The curator solves this by:
1. Running on a schedule (not on-demand)
2. Validating existing entries against sources
3. Marking stale entries
4. Re-indexing changed content

## Architecture

```
Sources (codebase, docs, APIs)
    ↓
Knowledge Curator topology (scheduled trigger)
    ├── ingester: scans sources → indexes to vector store
    └── validator: checks entries against sources → marks stale
    ↓
Vector store (Qdrant, Pinecone, etc. via MCP)
    ↓
Worker topologies (code review, solution advisor, etc.)
    └── query-knowledge-base skill (read-only)
```

SwarmKit doesn't build a knowledge base — it **orchestrates existing
ones** through MCP. The topology is the value, not the storage.

## Curator topology

```yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: knowledge-curator
  version: 0.1.0
agents:
  root:
    id: curator
    role: root
    model:
      provider: anthropic
      name: claude-sonnet-4-6
    prompt:
      system: |
        You maintain the knowledge base. Your job is to keep it
        current, accurate, and relevant. You do not answer questions
        or do work — you ensure the agents who do have correct
        information.
    children:
      - id: ingester
        role: worker
        archetype: kb-ingester
      - id: validator
        role: worker
        archetype: kb-validator
```

### Ingester worker

Scans source repositories, documentation, and APIs for content.
Indexes into the vector store via MCP.

```yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: kb-ingester
  name: Knowledge Base Ingester
  description: |
    Scans source code, documentation, and API specs. Extracts
    meaningful chunks, adds provenance metadata (source path,
    commit hash, timestamp), and indexes to the vector store.
    Runs PII/secrets filtering before indexing.
role: worker
defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4-6
  skills:
    - scan-git-repo
    - ingest-documents
    - index-to-vector-store
  iam:
    base_scope: [kb:write, fs:read, git:read]
```

### Validator worker

Periodically checks existing KB entries against their sources.
Marks entries as stale if the source has changed.

```yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: kb-validator
  name: Knowledge Base Validator
  description: |
    Checks existing knowledge base entries against their original
    sources. Compares indexed_at timestamp with source modification
    time. Marks stale entries for re-indexing. Reports freshness
    statistics to the curator.
role: worker
defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4-6
  skills:
    - query-vector-store
    - check-source-freshness
    - mark-entry-stale
  iam:
    base_scope: [kb:read, kb:write, fs:read, git:read]
```

## KB entry data model

Every entry in the vector store has provenance metadata:

```json
{
  "id": "kb-00123",
  "content": "Order processing uses the BatchProcessor pattern...",
  "embedding": [0.12, -0.34, ...],
  "metadata": {
    "source_type": "code",
    "source_path": "src/orders/batch_processor.py",
    "source_commit": "a1b2c3d",
    "indexed_at": "2026-04-24T10:00:00Z",
    "staleness_check": "2026-04-24T09:55:00Z",
    "status": "current",
    "superseded_by": null
  }
}
```

Workers see the entry + when it was last verified. Entries older
than a configurable threshold are flagged in query results.

## IAM enforcement

| Role | Scopes | Can do |
|---|---|---|
| Curator (ingester + validator) | `kb:write`, `kb:read`, `fs:read`, `git:read` | Index, validate, mark stale |
| Worker topologies | `kb:read` | Query only |
| No agent | `kb:delete` | Never — entries are marked stale, not deleted |

Workers can **submit suggestions** to the curator ("I found something
that should be in the KB") via a coordination skill. The curator
decides whether to index it.

## PII/secrets governance

Governance fires at two points:

### Ingest-time filtering

Before content enters the KB, it passes through governance:

```
Source content → evaluate_action("kb:ingest", {content_hash, path})
  → PII detection (email, phone, SSN patterns)
  → Secret scanning (API keys, tokens, connection strings)
  → File exclusion (.env, credentials.*, secrets/)
  → Clean content → index to vector store
```

### Query-time governance

Before a query response reaches the user:

```
Agent queries KB → gets results → evaluate_action("kb:query_response")
  → Response redaction if sensitive patterns detected
  → Clean response → returned to agent
```

### Workspace configuration

```yaml
# workspace.yaml
knowledge:
  governance:
    pii_redaction: true
    secret_scanning: true
    excluded_paths:
      - "**/.env"
      - "**/credentials*"
      - "**/secrets/**"
      - "**/*.pem"
    content_rules:
      - pattern: "\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b"
        action: redact
        label: email
      - pattern: "\\b\\d{3}-\\d{2}-\\d{4}\\b"
        action: block
        label: ssn
      - pattern: "(sk-|ghp_|gsk_|xai-)[A-Za-z0-9]{20,}"
        action: block
        label: api-key
    freshness:
      max_age_days: 30
      warn_age_days: 7
```

All pattern matching uses AGT's policy engine (Tier 1 deterministic,
sub-millisecond). No LLM needed for PII/secret detection.

## MCP server integrations

The curator uses existing community MCP servers:

| Purpose | MCP server | Transport |
|---|---|---|
| Read code/files | `@modelcontextprotocol/server-filesystem` | stdio |
| Read git history | `@modelcontextprotocol/server-git` | stdio |
| Vector store (index + query) | `mcp-server-qdrant` (official) | stdio |
| Document ingestion (97+ formats) | `kreuzberg` | stdio |
| Enterprise docs | `mcp-atlassian` (Confluence/Jira) | sse |

All configured in `workspace.yaml` under `mcp_servers`.

## Scheduled trigger

The curator runs on a schedule, not on-demand:

```yaml
apiVersion: swarmkit/v1
kind: Trigger
metadata:
  id: kb-refresh
type: schedule
cron: "0 */6 * * *"
targets: [knowledge-curator]
```

Every 6 hours: ingester scans for new/changed content, validator
checks existing entries for staleness.

## How worker topologies use the KB

Worker topologies (code review, solution advisor, etc.) access the
KB through a read-only query skill:

```yaml
# skills/query-knowledge-base.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: query-knowledge-base
  name: Query Knowledge Base
  description: Semantic search over the indexed knowledge base.
category: capability
implementation:
  type: mcp_tool
  server: qdrant
  tool: search
iam:
  required_scopes: [kb:read]
```

The agent's archetype includes this skill:

```yaml
# archetypes/sterling-specialist.yaml
defaults:
  skills:
    - query-knowledge-base
    - code-diff-read
  prompt:
    system: |
      You are a Sterling OMS specialist. Query the knowledge base
      for architecture decisions, API contracts, and code patterns
      before answering. Ground your answers in the indexed sources.
```

## Implementation plan

1. **This PR:** design note
2. **Follow-up PRs:**
   - Reference skills: `scan-git-repo`, `query-vector-store`,
     `index-to-vector-store`, `check-source-freshness`
   - KB governance wiring in the compiler (ingest-time + query-time
     filtering)
   - Example workspace: `examples/knowledge-curator/`

## Non-goals (for now)

- **Publisher agent.** Dropped from initial design. Humans query the
  swarm, not the KB directly. Add later if users want human-browsable
  KB exports.
- **Multi-tenant KB.** Single workspace = single KB. Multi-workspace
  knowledge sharing is a v2 concern.
- **Custom embedding models.** Uses the vector store's default
  embeddings. Configurable embeddings are a follow-up.
