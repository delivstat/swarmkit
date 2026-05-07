---
title: LinkedIn post for SwarmKit
description: Hook-Story-Grit structure, Rynko voice, no domain-specific references
---

# LinkedIn Post

I asked our AI assistant how a specific feature works in our project.
It sent the question to two specialist agents at the same time.

One searched 17,000 product docs, configuration dumps, and API
references. The other grepped the source code, read specific line
ranges from a 2,200-line file, and traced the actual function calls
that build the API input.

Both ran in parallel. The coordinator merged both views — the design
context from one side, the actual code with line references from the
other — into a single answer.

507 requests that day. 1.9 million tokens. $0.33.

That number still surprises me. It's possible because each agent runs
on a different model. The router uses llama-3.3 at $0.10 per million
tokens — it's just deciding who handles each question. The workers
use deepseek-chat at $0.32 for the actual reasoning. All the tool
calls — grep, file reads, vector search, config lookups — run locally
and cost nothing.

I built SwarmKit because I kept rewriting the same LangGraph Python
every time I needed a new agent setup. The idea is simple: your agent
topology is a YAML file, not code. Change the YAML, the graph changes.
Add an agent, swap a model, assign a new tool — edit a config file,
not a codebase.

[IMAGE: cross-consultation-flow.png]

A few things I didn't expect to learn along the way.

Tool names drive model behaviour more than prompt instructions. I had
a documentation lookup tool in a code analyst's tool list. When users
asked about code, the model called the docs tool every time because
the name matched the query. The system prompt saying "don't use this
for code" was completely ignored. Removing the tool from the agent's
list fixed it immediately. I've learned to shape behaviour through
tool availability, not prompt engineering.

Models describe what they plan to do instead of doing it. After a
search found the right file, the model would respond with "Let me
examine the code..." and stop. Planning language, no action. I added
detection for this — if the response looks like intent rather than
execution, the runtime pushes back. Small thing, but it eliminated
an entire class of non-answers.

The framework can also author itself. Running swarmkit author skill
starts a conversation where the system helps create new capabilities,
validates the output, and corrects errors. Our workspace started with
11 skills and grew to 21 through this flow — each new skill came from
a gap we noticed during real use.

[IMAGE: cost-breakdown.png]

I'm still working through a question I don't have a clean answer to.
We built a tool that checks every file:line citation in an agent's
code analysis against the actual source — catches hallucinated line
numbers at zero cost. But it can't tell me whether the model's
interpretation of what that code does is correct. The line is real,
the code is real, but the explanation might still be wrong. How do
you verify understanding, not just reference accuracy?

github.com/delivstat/swarmkit

---

# First comment (post separately)

Open source, MIT license. The design doc is in the repo.

What we're working on next:
- Knowledge Curator — a persistent wiki that accumulates findings
  across conversations so agents don't start from scratch every time
- Installable expertise packages — swarmkit install @company/domain
  and your AI assistant gains that domain knowledge via MCP
- Parallel delegation shipped last week — when the root sends to
  multiple workers, they run concurrently

Docs: github.com/delivstat/swarmkit

---

# Reply plan (for first 3-5 likely comments)

**"How does this compare to CrewAI?"**
CrewAI is code-first — agents are Python classes. SwarmKit is
data-first — agents are YAML that the runtime interprets. The
trade-off is flexibility vs simplicity. CrewAI gives you full
Python control; SwarmKit gives you a config file that non-developers
can read and modify. Both use the same underlying idea of specialised
agents with tools.

**"What models work best?"**
We've tested with deepseek-chat (best value for tool use), llama-3.3
(good router), qwen3-235b (good for reasoning), and Claude Sonnet
(best overall but 10x the cost). The per-agent model control means
you can mix — cheap model for routing, capable model for the hard
thinking. Gemini Flash for vision tasks (diagram understanding).

**"$0.33 seems too low — what's the catch?"**
No catch, but it's because most of the work is tool calls (grep,
file read, ChromaDB search) that run locally for free. The LLM only
handles routing and synthesis. If every step was an LLM call, it
would be 10-20x more. The architecture deliberately pushes work to
deterministic tools.

**"Does it work with Ollama / local models?"**
Yes. Ollama is one of 7 built-in providers. Quality depends on the
model — llama-3.3 works well for routing, but local models struggle
with complex tool-use chains compared to deepseek or Claude.

**"Why not just use LangGraph directly?"**
For a 2-agent setup, LangGraph directly is fine. When you have 5
topologies, 12 archetypes, 21 skills, and 9 MCP servers — all with
different model configs and tool assignments — managing that in code
gets painful. The YAML layer is what makes it manageable.
