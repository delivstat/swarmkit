# Level 9: Conversations & Memory

Build agents that hold multi-turn conversations and remember across sessions.

## What you'll learn

- Multi-turn chat with `swarmkit chat`
- Conversation persistence and resume
- Workspace memory (MemoryStore and GBrainMemory)
- Memory-reader and memory-writer decision skills
- Cross-conversation context injection

## Multi-turn conversations

### 1. Start a chat

```bash
swarmkit chat . hello
```

This opens an interactive chat session. Type messages, get responses, and the agent remembers the full conversation history. Type `/quit` to exit.

### 2. Chat commands

Inside a chat session:

| Command | Action |
|---------|--------|
| `/quit` | Exit chat |
| `/clear` | Clear conversation history |
| `/history` | Show conversation turns |
| `/new` | Start fresh conversation |

### 3. Resume a conversation

```bash
# List saved conversations
swarmkit conversations .

# Resume by ID
swarmkit chat . hello --resume abc123

# Pick from a list
swarmkit conversations . --pick
```

Conversations are saved to `.swarmkit/conversations/` as JSON files.

## Workspace memory

Memory goes beyond conversations — it lets agents remember insights across different conversations, different users, and different sessions.

### 4. Enable memory

Add memory decision skills to your workspace:

```yaml
# workspace.yaml — add memory skills
governance:
  provider: mock
  decision_skills:
    - id: content-filter
      trigger: pre_input
      scope: "*"
    # Memory — reads prior context before each turn
    - id: memory-reader
      trigger: pre_input
      scope: "*"
      config:
        max_results: 5
        similarity_threshold: 0.15
        search_scope: all
    # Memory — saves insights after each turn
    - id: memory-writer
      trigger: post_output
      scope: "*"
      config:
        min_output_length: 100
```

### 5. How memory works

**After each turn (memory-writer):**
1. An LLM extracts structured insights from the conversation
2. Extracts: topic, context, key points, tags
3. Decides if the turn is "worth saving" (greetings = no, deep discussion = yes)
4. Saves to the memory store

**Before each turn (memory-reader):**
1. Searches the memory store for relevant prior context
2. If found, injects it into the agent's prompt:
   ```
   WORKSPACE MEMORY — relevant prior conversations:
   Topic: Career confusion
   Context: User was struggling with job change decision
   Key points:
     - Discussed dharma vs personal desire
     - User found the Arjuna analogy helpful
   ```
3. The agent references it naturally: "As we discussed previously..."

### 6. Two memory backends

**MemoryStore (default)** — local JSON file + TF-IDF search:
```
.swarmkit/memory.json
```
Zero setup. Works immediately. Good for single-user, local use.

**GBrainMemory** — auto-detected when GBrain MCP server is configured:
```yaml
mcp_servers:
  - id: gbrain
    transport: stdio
    command: ["gbrain", "serve"]
```

When SwarmKit sees a `gbrain` MCP server + memory decision skills, it automatically uses GBrainMemory instead of MemoryStore. GBrain provides:
- Hybrid vector + keyword search
- Graph relationships between memories
- Supabase/Postgres for production scale

### 7. Test memory

```bash
# Start a chat
swarmkit chat . hello

# Conversation 1:
You: I'm dealing with grief after losing my father
Agent: [responds with teaching about grief]

# Exit and start a new conversation
You: /quit

# Start another chat
swarmkit chat . hello

# Conversation 2:
You: I feel lost
Agent: "I remember we discussed grief before, when you were
dealing with your father's passing..."
```

The agent references the prior conversation because memory-reader found the relevant context.

### 8. Memory in serve mode

Memory works the same in serve mode — the HTTP API handles it:

```bash
swarmkit serve .

# POST /conversations — creates conversation
# POST /conversations/{id}/messages — sends message (memory auto-injected)
```

## Memory configuration

### memory-reader config

| Field | Default | Description |
|-------|---------|-------------|
| `max_results` | 5 | Max memories to inject per turn |
| `similarity_threshold` | 0.1 | Min score to include |
| `search_scope` | `user` | `user` (per-user), `all`, or `both` |

### memory-writer config

| Field | Default | Description |
|-------|---------|-------------|
| `min_output_length` | 50 | Skip extraction for short responses |

## Your workspace so far

```
my-swarm/
├── workspace.yaml          # memory-reader + memory-writer configured
├── .swarmkit/
│   ├── conversations/      # saved chat sessions
│   └── memory.json         # extracted insights
├── archetypes/
├── skills/
├── servers/
├── gates/
└── topologies/
```

## Next

[Level 10: Knowledge & RAG](10-knowledge-rag.md) — give your agents access to a knowledge base.
