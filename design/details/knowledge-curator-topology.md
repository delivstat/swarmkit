# Knowledge Curator Topology

**Status:** Design note — proposed for reference topology  
**Design ref:** §5.4 (first-class artifacts), §6.2 (persistence skills), §4.2 (reference topologies)

## Problem

Every SwarmKit workspace accumulates knowledge through conversations —
an architect explains how sourcing rules work, a developer traces a
code flow, a Confluence page gets downloaded and analysed. But these
findings vanish into conversation history. The next time someone asks
a related question, the agents start from scratch: re-searching docs,
re-grepping code, re-querying config.

Naive RAG over raw documents helps with discovery but doesn't capture
the synthesised understanding that emerges from agent analysis. A grep
result is not the same as "this class builds the manageSourcingRule XML
input using createElement/setAttribute with these specific attributes."

## Solution

A Knowledge Curator topology that maintains a persistent, structured
wiki — a layer between raw sources and conversation. Inspired by
Karpathy's "LLM-maintained wiki" pattern: raw documents are immutable,
the wiki is a compounding artifact that accumulates cross-referenced
knowledge pages, and the schema defines conventions.

### Three-layer architecture

```
Layer 1: Raw Sources (immutable)
├── Product docs (ChromaDB)
├── CDT config (JSON indexes)
├── API javadocs (structured)
├── Project code (filesystem)
├── Confluence pages
└── Jira tickets

Layer 2: Wiki (LLM-maintained, persistent)
├── index.md                    # auto-generated catalogue
├── log.md                      # append-only change log
├── sourcing-rules.md           # topic page
├── order-creation-flow.md      # topic page
├── ship-from-store-design.md   # topic page
└── ...

Layer 3: Schema (conventions)
├── wiki-schema.md              # page structure, frontmatter, linking
└── workspace.yaml              # topology + skill definitions
```

### Wiki page format

Each wiki page is a markdown file with frontmatter:

```markdown
---
title: Sourcing Rules Management
sources:
  - chromadb:project-docs/OMS_Functional.docx
  - cdt:YFS_SOURCING_RULE_HDR
  - code:SourcingRuleFileUploadAgent.java
last_updated: 2026-05-05
related:
  - order-creation-flow
  - inventory-cache-integration
confidence: high
---

# Sourcing Rules Management

## Summary
Sourcing rules are loaded from SAP via the SourcingRuleFileUploadAgent...

## Data Flow
SAP → OMS (JMS queue PH2_SEND_SOURCINGRULE_FILE_TO_IC_Q) → IC

## Key Code
At SourcingRuleFileUploadAgent.java:2095, the method builds the XML...
```

### Three operations

**1. Ingest**

Feed a new source (document, Confluence page, Jira ticket) to the
curator. The curator reads the source, identifies topics, and
creates or updates wiki pages. Cross-references are maintained
automatically. Changes are logged to `log.md`.

```bash
swarmkit run . knowledge-curator \
  --input "Ingest the Confluence page on OMS deployment architecture (ID: 3469803531)"
```

**2. Query-and-persist**

During normal conversation (any topology), when an agent produces a
high-quality synthesised answer, it writes or updates the relevant
wiki page via the `wiki-write` skill. Future queries on the same
topic find the wiki page first and skip the expensive tool chain.

The wiki acts as a conversation-scoped cache that persists across
sessions — similar to the tool result cache (v1.0.32) but at the
knowledge level, not the tool level.

**3. Lint**

Periodic health check of the wiki. Detects:
- Contradictions between pages
- Stale claims (source documents updated since the page was written)
- Orphan pages (no cross-references)
- Missing topics (frequently queried but no wiki page exists)
- Data gaps (pages citing sources that no longer exist)

```bash
swarmkit run . knowledge-curator --input "Lint the knowledge base"
```

## Topology design

```yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: knowledge-curator
  description: >
    Maintains a persistent wiki of accumulated knowledge. Ingests new
    sources, creates cross-referenced topic pages, and lints for
    quality. Any workspace can add this topology to build institutional
    knowledge that persists across conversations.
agents:
  root:
    id: root
    role: root
    archetype: knowledge-coordinator
    children:
      - id: curator
        role: worker
        archetype: knowledge-curator
      - id: indexer
        role: worker
        archetype: knowledge-indexer
      - id: linter
        role: worker
        archetype: knowledge-linter
```

### Agent responsibilities

**Knowledge Coordinator (root)**
- Routes ingest/query/lint requests to the right worker
- Ensures curator and indexer stay in sync

**Knowledge Curator (worker)**
- Reads raw sources (all knowledge skills — search, read, grep)
- Creates and updates wiki pages with frontmatter
- Maintains cross-references between related pages
- Logs all changes to `log.md`
- Reads existing wiki pages before writing (avoids duplicates)

**Knowledge Indexer (worker)**
- Rebuilds `index.md` after curator changes
- Categorises pages by topic area
- Generates a summary for each page (one line)
- Detects and flags duplicate topics

**Knowledge Linter (worker)**
- Checks page freshness (source modification dates vs page dates)
- Finds contradictions between pages
- Reports orphan pages and missing cross-references
- Suggests new topics based on skill gap logs

## Skills

| Skill | Category | Server | Description |
|-------|----------|--------|-------------|
| wiki-read | capability | wiki-fs | Read a wiki page by name |
| wiki-write | persistence | wiki-fs | Create or update a wiki page |
| wiki-search | capability | wiki-fs | Search wiki pages by content |
| wiki-list | capability | wiki-fs | List all wiki pages with summaries |
| wiki-log | persistence | wiki-fs | Append to the change log |

The `wiki-fs` MCP server is a filesystem server pointed at the wiki
directory. The skills wrap `read_file`, `write_file`, `search_files`,
and `list_directory` with wiki-specific conventions (frontmatter
validation, cross-reference checking).

## Integration with existing topologies

Any workspace topology can opt in to wiki-aware behaviour by adding
`wiki-read` and `wiki-search` to its archetypes. The agent checks the
wiki before searching raw sources:

```
User asks question
    ↓
Agent calls wiki-search (fast, pre-synthesised)
    ↓
Found? → Use wiki content + cite the page
Not found? → Fall through to raw source search (ChromaDB, grep, etc.)
    ↓
Produce answer
    ↓
If answer is high quality, call wiki-write to persist it
```

This is opt-in — workspaces that don't need persistent knowledge skip
the wiki layer entirely.

## Directory structure

```
workspace/
├── knowledge/                    # the wiki
│   ├── index.md                  # auto-generated catalogue
│   ├── log.md                    # append-only change log
│   └── topics/                   # topic pages
│       ├── sourcing-rules.md
│       ├── order-creation-flow.md
│       └── ...
├── topologies/
│   └── knowledge-curator.yaml
├── archetypes/
│   ├── knowledge-coordinator.yaml
│   ├── knowledge-curator.yaml
│   ├── knowledge-indexer.yaml
│   └── knowledge-linter.yaml
└── skills/
    ├── wiki-read.yaml
    ├── wiki-write.yaml
    ├── wiki-search.yaml
    ├── wiki-list.yaml
    └── wiki-log.yaml
```

## Cost model

- **Ingest:** 1-2 LLM calls per source (read + write pages). ~$0.01-0.05
  per source depending on size.
- **Query-and-persist:** Wiki read is $0 (filesystem). Wiki write is
  one tool call when the agent decides to persist.
- **Lint:** One LLM call per check. Full lint ~$0.05-0.10.
- **Ongoing savings:** Wiki hits avoid expensive multi-tool chains
  (ChromaDB + grep + read-file-lines + API schema). A wiki read is
  ~50 tokens vs ~5000 tokens for a full tool chain.

## Non-goals

- **Not a RAG replacement.** The wiki complements ChromaDB, not
  replaces it. Raw document search is still needed for discovery.
- **Not auto-updating.** The wiki doesn't watch for source changes.
  Lint detects staleness; humans or cron trigger re-ingestion.
- **Not multi-tenant.** One wiki per workspace. Cross-workspace
  knowledge sharing is a v2.0 concern.

## Open questions

1. Should wiki pages be indexed in ChromaDB for semantic search, or
   is filename + frontmatter search sufficient?
2. Should the curator run as a background trigger (cron) or only
   on-demand?
3. How to handle conflicting updates from concurrent conversations?
