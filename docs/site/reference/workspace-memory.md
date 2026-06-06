# Workspace memory

Workspace memory lets agents remember context across conversations. Insights from past conversations are automatically extracted and injected into future ones.

## How it works

Two decision skill hooks run at the compiler level:

1. **memory-writer** (`post_output`) — after each agent response, an LLM extracts structured insights (topic, context, key points, tags) and saves them
2. **memory-reader** (`pre_input`) — before each agent response, searches saved memories by TF-IDF similarity and injects relevant context

## Configuration

Add memory decision skills to `workspace.yaml`:

```yaml
governance:
  decision_skills:
    - id: memory-reader
      trigger: pre_input
      scope: "advisor"
      config:
        max_results: 5
        similarity_threshold: 0.15
        search_scope: all
    - id: memory-writer
      trigger: post_output
      scope: "advisor"
      config:
        min_output_length: 100
```

### memory-reader config

| Field | Default | Description |
|-------|---------|-------------|
| `max_results` | 5 | Maximum memories to inject |
| `similarity_threshold` | 0.1 | Minimum TF-IDF score to include |
| `search_scope` | `user` | `user` (per-user), `all` (global), `both` |

### memory-writer config

| Field | Default | Description |
|-------|---------|-------------|
| `min_output_length` | 50 | Skip extraction for short responses |

## Storage

Memories are stored as JSON at `.swarmkit/memory/memories.json`. Each entry contains:

- `topic` — short label for the conversation topic
- `context` — what the user was asking and why
- `key_points` — list of important takeaways
- `tags` — semantic tags for retrieval
- `source_agent` — which agent produced the insight
- `user` — user identifier (when available)
- `session_id` — conversation session ID

## GBrain backend

For production use, configure GBrain as the memory backend. GBrain provides hybrid search (semantic + keyword), graph relationships, and fact extraction via MCP tools.

Add GBrain as an MCP server in `workspace.yaml`:

```yaml
mcp_servers:
  - id: gbrain
    transport: stdio
    command: ["gbrain", "serve"]
```

The agent can then use `brain-write` and `brain-search` tools for persistent knowledge graph memory.

## What gets saved

The memory-writer uses an LLM to determine whether a conversation turn is worth saving. Trivial exchanges (greetings, clarifications) are skipped. Substantive conversations that contain:

- Life guidance discussions
- Technical decisions
- User preferences or context
- Important facts or situations

are extracted and saved for future reference.

## How context is injected

When memory-reader finds relevant prior conversations, it prepends them to the agent's input as:

```
WORKSPACE MEMORY — relevant prior conversations for this user:

Topic: grief and loss
Context: User was dealing with the loss of a parent
Key points:
  - Discussed Gita 2:47 on detachment
  - User found the Nachiketa story helpful

---

Use this context naturally. Reference prior conversations when relevant.
Do not explicitly mention "memory" or "database".
```

The agent sees this as natural context and can reference prior conversations organically.
