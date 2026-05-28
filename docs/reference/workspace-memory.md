# Workspace Memory

Agents that remember across conversations. Every conversation builds
knowledge that future conversations can access — the agent gets smarter
the more it's used.

## What it does

After each conversation turn, the memory-writer extracts structured
insights (topic, context, key points, tags) and saves them. Before the
next conversation, the memory-reader searches for relevant prior context
and injects it into the agent's prompt. The agent sees this as its own
recollection — "As we discussed previously..."

```
Conversation 12: User asks about grief
  → Agent responds with Katha Upanishad guidance
  → Memory-writer saves: {topic: "grief", key_points: ["Katha 2.19"]}

Conversation 28: User asks about letting go
  → Memory-reader finds the grief conversation
  → Agent sees: "WORKSPACE MEMORY: grief discussion, Katha 2.19 resonated"
  → Agent responds: "Building on what you shared about your friend's
    passing, the same teaching applies here..."
```

---

## Quick start

### 1. Add memory bindings to workspace.yaml

```yaml
governance:
  decision_skills:
    - id: memory-reader
      trigger: pre_input
      scope: "*"
      config:
        search_scope: user       # user, shared, or both
        max_results: 5
        similarity_threshold: 0.1
    - id: memory-writer
      trigger: post_output
      scope: "*"
      config:
        min_output_length: 50    # skip trivial exchanges
```

That's it. No other setup needed for the local store.

### 2. Run your workspace

```bash
swarmkit chat my-workspace/ my-topology
```

Memories are saved to `.swarmkit/memory.json` in your workspace directory.
They persist across server restarts and CLI sessions.

---

## GBrain backend (production)

For production deployments, workspace memory uses GBrain instead of the
local JSON store. GBrain provides:

- **Hybrid search** (vector + keyword) instead of TF-IDF
- **Graph relationships** between memory nodes
- **Built-in fact extraction** with deduplication
- **Shared storage** across distributed workers (Supabase/Postgres)

### Setup GBrain

```bash
# Initialize a brain in your workspace
cd my-workspace/
gbrain init --pglite    # local PGLite (no server needed)
# or
gbrain init --supabase  # production Supabase
```

### Add GBrain to workspace.yaml

```yaml
mcp_servers:
  - id: gbrain
    transport: stdio
    command: ["gbrain", "serve"]

governance:
  decision_skills:
    - id: memory-reader
      trigger: pre_input
      scope: "*"
      config:
        search_scope: user
        max_results: 5
    - id: memory-writer
      trigger: post_output
      scope: "*"
```

When a `gbrain` MCP server is configured, the memory hooks automatically
use `GBrainMemory` instead of the local store.

### GBrain MCP tools used

| Tool | Purpose |
|------|---------|
| `put_page` | Save memory as a markdown page with frontmatter |
| `query` | Hybrid vector + keyword search for relevant memories |
| `add_tag` | Tag memory pages for filtering |
| `add_link` | Create cross-session relationships |
| `extract_facts` | GBrain's built-in LLM fact extraction |
| `recall` | Per-entity hot memory retrieval |
| `delete_page` | Soft-delete with 72h recovery window |

### Memory page format in GBrain

```markdown
---
type: memory
created: 2026-05-28T03:34:26.822276+00:00
user: srijith
session: conv-28
agent: advisor
tags: [attachment, isha-upanishad, letting-go]
---

# Letting go of attachment

User struggling with attachment to business outcome

## Key Points
- Isha Upanishad — enjoy without possessing
- Connected to prior career dharma discussion
```

Pages are stored at `memory/{user}/{timestamp}` slugs with `type: memory`
for easy filtering.

---

## Configuration reference

### memory-reader (pre_input)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `search_scope` | string | `"user"` | `"user"` = user-specific only, `"shared"` = workspace-wide, `"both"` = both |
| `max_results` | int | 5 | Maximum memories to inject |
| `similarity_threshold` | float | 0.1 | Minimum TF-IDF score for a match |

### memory-writer (post_output)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `min_output_length` | int | 50 | Skip extraction for short outputs (greetings, clarifications) |

### Scoping by agent

You can restrict memory to specific agents:

```yaml
governance:
  decision_skills:
    - id: memory-writer
      trigger: post_output
      scope: "advisor,counselor"   # only these agents write memories
    - id: memory-reader
      trigger: pre_input
      scope: "advisor"             # only advisor reads memories
```

---

## How it works internally

### Memory-writer flow

```
Agent produces output
  → post_output trigger fires
  → Memory-writer sends (user_input + agent_output) to LLM
  → LLM extracts: {topic, context, key_points, tags, worth_saving}
  → If worth_saving=true → saved to MemoryStore/GBrain
  → If worth_saving=false → skipped (greetings, clarifications)
  → Decision skill returns verdict="pass" (never blocks output)
```

### Memory-reader flow

```
User sends a message
  → pre_input trigger fires
  → Memory-reader searches store for relevant prior conversations
  → If matches found → prepends context to agent's input:
    "WORKSPACE MEMORY — relevant prior conversations..."
  → Agent sees the context naturally in its prompt
  → Agent can reference past sessions ("As we discussed...")
```

### What the agent sees

When memory context is injected, the agent's input looks like:

```
WORKSPACE MEMORY — relevant prior conversations for this user:

Topic: Grief and loss of a friend
Context: User dealing with sudden loss of a close friend
Key points:
  - Katha Upanishad 2.19 resonated deeply
  - Found comfort in impermanence teaching
(from session conv-12)

Use this context naturally. Reference prior conversations when
relevant ("As we discussed previously..." or "Building on what
you shared about..."). Do not explicitly mention "memory" or
"database" — treat it as your own recollection.

[actual user message follows]
```

---

## Examples across domains

### Vedanta advisor

```
Session 12: "I lost my friend" → Katha 2.19 guidance → saved
Session 15: "Should I leave my job?" → Gita 2.47 guidance → saved
Session 28: "How to let go of attachment?" → searches memory →
  finds grief + career sessions → responds: "Building on what you
  shared about your friend and your career questions, the Isha
  Upanishad teaches tena tyaktena bhunjitha..."
```

### Sterling OMS

```
Session 1: "Trace return order config" → 12 MCP tool calls → saved
Session 5: "How does return processing work?" → searches memory →
  finds cached CDT trace → skips 12 tool calls, uses stored findings
  (saves ~2000 tokens and 30 seconds)
```

### Code review

```
Session 1: "Review PR #49" → finds auth bypass → saved
Session 3: "Review PR #55" → searches memory → finds prior auth issue →
  "Note: PR #49 had a similar auth pattern that was flagged. Checking
  if this PR has the same issue..."
```

---

## Privacy and data management

### User data isolation

User-specific memories are tagged with the user identity. When
`search_scope: user` is set, only that user's memories (plus shared
memories) are returned. Other users' memories are never surfaced.

### Deletion

```python
from swarmkit_runtime.memory import MemoryStore
from pathlib import Path

store = MemoryStore(Path("my-workspace/"))

# Delete specific memory
store.delete("mem-20260528T120000-0")

# Delete all memories for a user (GDPR)
removed = store.delete_user("alice")
# Output: Deleted 2 memories for alice
```

### What's saved

Only **structured insights** are saved, not raw conversation transcripts.
A 200-message conversation becomes 5-10 memory entries with:
- Topic label
- Context summary
- Key points (bullet list)
- Tags for retrieval

Trivial exchanges (greetings, "hello", "thanks") are automatically
skipped (`worth_saving: false`).

---

## Real test outputs

All outputs below from actual runs. Date: 2026-05-28, version 1.2.60.

### Memory store with search

```
Total memories: 3
Memories for srijith: 3

Search: "career dharma duty"
  [0.120] Career doubt and dharma (session conv-15)

Search: "attachment letting go"
  [0.395] Letting go of attachment to outcomes (session conv-28)

Status: 3 entries, users: ['srijith']
Top tags: [('grief', 1), ('loss', 1), ('katha-upanishad', 1), ...]
```

### Context injection (what the agent sees)

```
WORKSPACE MEMORY — relevant prior conversations for this user:

Topic: Grief and loss of a friend
Context: User dealing with sudden loss of a close friend
Key points:
  - Katha Upanishad 2.19 resonated deeply
  - Found comfort in impermanence teaching
(from session conv-12)

Use this context naturally. Reference prior conversations when relevant
("As we discussed previously..." or "Building on what you shared
about..."). Do not explicitly mention "memory" or "database" — treat
it as your own recollection.
```

### Insight extraction (LLM output)

```
Memory saved:
  Topic:   Letting go of attachment
  Context: User struggling with attachment to business outcome
  Points:  ['Isha Upanishad — enjoy without possessing',
            'Connected to prior career dharma discussion']
  Tags:    ['attachment', 'isha-upanishad', 'letting-go']
  User:    srijith
  Session: conv-28
  Agent:   advisor
```

### Persistence across restarts

```
Session 1: saved 2 memories
Session 2 (after restart): loaded 2 memories
  - Career dharma (tags: ['career'])
  - Grief discussion (tags: ['grief'])
Search "grief" after restart: 1 result(s)
```

### User deletion (GDPR)

```
Before: 3 memories (alice=2, bob=1)
Deleted 2 memories for alice
After: 1 memories (alice=0, bob=1)
```

---

## Limitations

- **TF-IDF search (local store)** — keyword matching, not semantic.
  Use GBrain for production-quality vector search.
- **No conversation-level context** — memories are per-turn, not
  per-conversation. A long conversation may generate many small entries.
- **LLM extraction cost** — each turn costs one Haiku-class LLM call
  (~$0.001) for insight extraction. Trivial turns are auto-skipped.
- **No automatic linking** — cross-session links are based on tag overlap
  in the local store. GBrain provides richer graph-based linking.
