---
title: Workspace memory — persistent knowledge graph that grows with use
description: Framework-level pattern where agents read from and write to a per-workspace knowledge graph. Domain knowledge is curated; operational memory is agent-written and grows across conversations.
tags: [memory, gbrain, knowledge, persistence]
status: design
---

# Workspace memory

## Problem

SwarmKit agents today are stateless across conversations. Every new
conversation starts from zero — the agent doesn't know what was discussed
last week, what was already investigated, or what the user cares about.

This wastes tokens (re-discovering the same information), misses
connections (related problems across sessions), and prevents the agent
from developing expertise about its specific deployment context.

## Insight

The pattern is universal — not domain-specific:

- **Vedanta advisor:** remembers a user's spiritual journey. "When we
  discussed grief last month, Katha 2.19 resonated with you. The same
  teaching applies to this attachment question."
- **Sterling OMS:** remembers past investigations. "We already traced
  this config path in PROJ-100 — the PaymentDetailsList feed requires
  SAP allocation. Here's what we found." Caches CDT lookups, API
  patterns, code discoveries.
- **Rynko content:** remembers client voice, past validations, what
  failed review and why.
- **Any workspace:** the agent gets smarter the more it's used.

## Design

### Two-layer knowledge graph

Every workspace gets a knowledge graph (GBrain instance) with two layers:

```
Workspace Knowledge Graph
├── domain knowledge (curated, shared, read-mostly)
│   ├── documentation, APIs, configs
│   ├── reference material (verses, specs, standards)
│   └── curated by humans or ingestion scripts
└── operational memory (agent-written, growing, per-user or shared)
    ├── conversation insights (what was discussed, what resonated)
    ├── discovered facts (configs found, patterns identified)
    ├── cross-references (links between sessions, topics, users)
    └── written automatically by agents after each conversation
```

**Domain knowledge** is what the agent knows about the domain — loaded
once, updated occasionally by humans or ingestion pipelines. This already
exists (vedanta wisdom blocks, Sterling CDT data, Rynko docs).

**Operational memory** is what the agent learns from doing its job —
accumulated automatically across every conversation. This is new.

### How agents write operational memory

A `post_output` decision skill fires after each conversation turn.
It extracts structured insights and writes them to GBrain:

```yaml
# workspace.yaml
governance:
  decision_skills:
    - id: memory-writer
      trigger: post_output
      scope: "*"
      config:
        memory_graph: gbrain
```

The memory-writer skill:
1. Receives the agent's output and the user's input
2. Extracts structured facts: topic, context, key findings, user reaction
3. Links to prior memory nodes (if this topic was discussed before)
4. Writes to GBrain via `put_page` with appropriate tags

```python
# What the memory-writer produces per turn
{
    "type": "conversation_insight",
    "user": "srijith",
    "session_id": "conv-28",
    "topic": "letting-go-of-attachment",
    "context": "user asking about detachment from career outcomes",
    "key_points": [
        "connected to Isha Upanishad tena tyaktena bhunjitha",
        "user resonated with framing of 'hold loosely'"
    ],
    "related_sessions": ["conv-12"],  # linked to prior grief discussion
    "emotional_state": "reflective",
    "tags": ["attachment", "career", "isha-upanishad"]
}
```

### How agents read operational memory

A `pre_input` step searches the knowledge graph before responding:

```yaml
governance:
  decision_skills:
    - id: memory-reader
      trigger: pre_input
      scope: "*"
      config:
        memory_graph: gbrain
        search_scope: user     # user-specific or workspace-wide
        max_results: 5
```

The memory-reader skill:
1. Takes the user's new input
2. Searches GBrain for relevant prior conversations (semantic search)
3. Injects relevant context into the agent's prompt
4. The agent sees: "Previous context for this user: ..."

### What gets written (by domain)

| Domain | Operational memory examples |
|--------|-----------------------------|
| **Vedanta** | Topics discussed, verses that resonated, user's life context, emotional journey, linked sessions |
| **Sterling** | Config paths discovered, API patterns found, ticket investigations, code references, deployment patterns |
| **Code review** | Past review findings, recurring issues per author, codebase patterns, test coverage gaps |
| **Content** | Client voice preferences, failed review reasons, approved patterns, brand guidelines learned |

### Cache behavior

Operational memory naturally acts as a cache for expensive discoveries:

```
First conversation:
  → Agent spends 12 tool calls tracing CDT config path
  → Memory-writer stores: "PaymentDetailsList requires SAP allocation feed,
    traced via CDT tables ORDER_LINE → PAYMENT → ALLOCATION"

Next conversation (same topic):
  → Memory-reader finds the cached discovery
  → Agent skips the 12 tool calls, uses the stored finding
  → Saves ~2000 tokens and 30 seconds
```

This isn't explicit caching — the agent naturally finds its own prior
work in the knowledge graph and builds on it instead of starting over.

### User-specific vs shared memory

Two scopes:

- **User-specific memory** — tagged with user identity. Only surfaced
  when that user is in conversation. Personal journey, preferences,
  prior context. Vedanta advisor needs this.
- **Shared memory** — available to all users. Discovered facts about
  the domain (CDT paths, API patterns, code conventions). Sterling
  needs this.

Configurable per workspace:

```yaml
governance:
  decision_skills:
    - id: memory-writer
      config:
        scope: user          # or: shared, both
```

### Privacy considerations

- User-specific memory is PII. Must be deletable on request.
- `swarmkit memory list --user srijith` — show stored memories
- `swarmkit memory delete --user srijith --session conv-12` — delete specific
- `swarmkit memory forget --user srijith` — delete all for a user
- Audit log records all memory writes (who wrote, when, what tags)
- Memory nodes inherit the workspace's audit redaction rules

### Relationship to existing features

| Feature | Relationship |
|---------|-------------|
| **GBrain** | Workspace memory IS a GBrain instance. Same MCP server, same `put_page`/`search` tools. New `conversation_insight` page type. |
| **Decision skills** | Memory-writer is a `post_output` skill. Memory-reader is a `pre_input` skill. Both use existing trigger infrastructure. |
| **Conversations** | Memory is indexed by conversation/session ID. Resume + memory = full context restoration. |
| **Checkpointer** | Checkpointer stores LangGraph state (messages, tool calls). Memory stores extracted insights. Different granularity — checkpointer is per-turn state, memory is distilled knowledge. |
| **Structured output** | Memory-writer benefits from structured agent output — `findings` with `source` fields map directly to memory nodes with provenance. |
| **Audit log** | Memory writes are auditable events. The audit log tracks what was written; the memory graph tracks the knowledge itself. |

### What this is NOT

- **Not a RAG pipeline.** RAG retrieves from a static corpus. Workspace
  memory grows from agent activity. The retrieval mechanism is similar
  (semantic search) but the corpus is dynamic.
- **Not conversation history.** The checkpointer stores raw message
  history. Memory stores extracted, structured insights. A 200-message
  conversation becomes 5-10 memory nodes.
- **Not a user profile.** Memory captures what happened in conversations,
  not demographic data. It's experiential, not categorical.

## Implementation path

### Phase 1: Memory-writer decision skill

- New skill YAML: `memory-writer` with `trigger: post_output`
- Uses existing GBrain `put_page` MCP tool
- Structured extraction prompt: topic, context, key_points, tags
- Session linking via GBrain `search` before write

### Phase 2: Memory-reader decision skill

- New skill YAML: `memory-reader` with `trigger: pre_input`
- Searches GBrain for user's prior sessions (semantic similarity)
- Injects relevant context as system message prefix
- Configurable `max_results` and `search_scope`

### Phase 3: Cross-session linking

- GBrain edges between related memory nodes
- Topic clustering (automatic tagging via embeddings)
- Timeline view: user's journey over time

### Phase 4: Memory management CLI

- `swarmkit memory list` — browse stored memories
- `swarmkit memory search` — semantic search across memories
- `swarmkit memory delete` — GDPR-style deletion
- `swarmkit memory stats` — memory growth metrics

## Workspace config

```yaml
# workspace.yaml — full memory configuration
governance:
  decision_skills:
    - id: memory-writer
      trigger: post_output
      scope: "*"
      config:
        memory_graph: gbrain
        write_scope: both       # user, shared, or both
        min_turn_length: 2      # don't write for single-turn Q&A
    - id: memory-reader
      trigger: pre_input
      scope: "*"
      config:
        memory_graph: gbrain
        search_scope: user      # user, shared, or both
        max_results: 5
        similarity_threshold: 0.7

mcp_servers:
  - id: gbrain
    transport: stdio
    command: ["npx", "gbrain-mcp"]
    env:
      DATABASE_URL: "${database_url}"
```

## Open questions

1. **Memory quality** — the memory-writer is an LLM. It might extract
   irrelevant or incorrect insights. Should there be a validation step?
   Or trust the model and let users curate via the CLI?
2. **Memory volume** — how much memory per conversation? Per user? At
   what point does search relevance degrade from too many nodes?
3. **Memory freshness** — old memories may be outdated. Should memories
   have a confidence decay over time? Or explicit "verified" flags?
4. **Multi-user conversations** — if two users interact with the same
   workspace, should their memories be isolated or cross-referenced?
5. **Memory in the UI** — the dashboard could show a memory timeline
   per user. The topology composer could show which agents write/read
   memory. Future UI surface.
