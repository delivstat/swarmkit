---
title: LinkedIn post for SwarmKit
description: Hook-Story-Grit structure, outcome-focused, one image max
---

# LinkedIn Post

I asked our AI assistant how a specific feature works in our project.

It didn't look it up in one place. It sent the question to two
specialist agents running in parallel — one searched 17,000 documents,
configuration data, and API references while the other read the actual
source code line by line.

Both finished in under 10 seconds. The answer cited real file paths,
real line numbers, and real configuration values — not generic
documentation.

Total cost for 500+ queries that day: $0.33.

That's not a typo. Here's why the economics work.

Each agent runs on a different model. The router uses a $0.10/M model
— it's just deciding who handles the question, not answering it. The
workers use a $0.32/M model for the actual reasoning and tool use.
Every tool call — searching documents, reading files, querying
configuration — runs locally and costs nothing.

The expensive model doesn't touch simple tasks. The cheap model doesn't
touch hard ones. One line in a config file controls which model each
agent uses.

I built this framework because I kept rewriting the same Python every
time I needed a different agent setup. Now the agent topology is a
YAML file — who exists, who reports to whom, what tools they have,
which model they use. Change the file, the system changes. No code
to rewrite.

[IMAGE: cross-consultation-flow.png]

What you can actually do with this today:

→ Ask a question that spans docs, code, and configuration — get one
  merged answer with citations from all three

→ Review a Jira ticket, download its attachments, and analyse the
  design documents — in a single conversation

→ Search Confluence, download pages as PDF with images preserved,
  and have a vision model describe the architecture diagrams

→ Create new agent capabilities through conversation — describe what
  you need, the system generates the YAML, validates it, and deploys

→ Switch models mid-conversation to compare quality and cost —
  /model deepseek/deepseek-chat vs /model anthropic/claude-sonnet-4-6

The workspace I run in production grew from 11 capabilities to 21 in
two weeks. Not because I wrote more code — because the agents
identified gaps during real use and I authored new skills through the
same conversational interface.

One thing I'm still figuring out: when an agent cites a specific line
of code in its analysis, we can verify the line exists. But we can't
automatically verify that the agent's interpretation of what that code
does is correct. The reference is real. The explanation might not be.

How are others solving this last-mile verification problem?

github.com/delivstat/swarmkit

---

# First comment (post separately)

Open source, MIT license. Installs in one command:
uv tool install swarmkit-runtime

Built on LangGraph, 7 model providers (OpenRouter, Anthropic, OpenAI,
Google, Groq, Together, Ollama), MCP tool servers for Jira,
Confluence, ChromaDB, code search, and PDF reading with vision.

Full design doc in the repo for anyone who wants the architectural
reasoning.

---

# Reply plan

**"What outcomes have you seen vs a single RAG pipeline?"**
The cross-consultation pattern (two specialists merging perspectives)
produces answers that a single pipeline can't. A docs-only search
misses the code. A code-only grep misses the design intent. The
merged answer has both — and costs under a cent per query.

**"How does this compare to CrewAI / AutoGen?"**
Those are code-first — agents are Python classes. SwarmKit is
data-first — agents are YAML. The trade-off: they give you full
programmatic control, we give you a config file anyone can read.

**"$0.33 seems too low"**
Most work is deterministic tool calls (grep, file read, vector
search) that run locally for free. The LLM only handles routing
and synthesis. If every step was an LLM call it would be 10-20x more.

**"Can I use this for X?"**
If X involves multiple knowledge sources, specialised agents, and
tool use — yes. The framework is general-purpose. The enterprise
workspace is just one example.

**"How do you handle hallucination?"**
Three layers: tool result caching (same call returns cached result),
citation verification (checks file:line references against actual
source), and grounding rules in the agent prompt (must read code
before citing it). Not foolproof, but raises the floor significantly.
