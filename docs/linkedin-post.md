---
title: LinkedIn post for SwarmKit
description: Hook-Story-Grit structure, Rynko voice
---

# LinkedIn Post

I asked our AI assistant how sourcing rules work in our Sterling OMS
project. It sent the question to two specialist agents at the same time.

One searched 17,000 product docs, the CDT configuration dump, and
1,006 API references. The other grepped the Java source, read lines
2080-2216 of a specific class, and traced the actual createElement
and setAttribute calls that build the XML input.

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
a tool called get-api-input-xml in the developer's list. When users
asked about XML in the project code, the model called that tool every
time — regardless of what the system prompt said. It returns generic
product documentation, not what the code actually builds. Removing the
tool from the agent's list fixed it immediately. I've learned to shape
behaviour through tool availability, not prompt engineering.

Models describe what they plan to do instead of doing it. After grep
found the right file, the model would respond with "Let me examine
the code..." and stop. Planning language, no action. I added detection
for this — if the response looks like intent rather than execution,
the runtime pushes back. Small thing, but it eliminated an entire
class of non-answers.

The framework can also author itself. Running swarmkit author skill
starts a conversation where the system helps create new capabilities,
validates the output, and corrects errors. Our Sterling workspace
started with 11 skills and grew to 21 through this flow — each new
skill came from a gap we noticed during real use.

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

Open source, MIT license. The design doc is in the repo if you want
the architectural reasoning behind the decisions.

What we're working on next:
- Knowledge Curator — a persistent wiki that accumulates findings
  across conversations so agents don't start from scratch every time
- W2A sensor integration — agents that notice events (Jira ticket
  created, Confluence updated) and act without a human prompt
- Installable expertise packages — swarmkit install @company/domain
  and your AI assistant gains that domain knowledge

Docs: github.com/delivstat/swarmkit
Discord: (add link when ready)

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
deterministic tools and only uses the LLM where reasoning is needed.

**"Does it work with Ollama / local models?"**
Yes. Ollama is one of the 7 built-in providers. You can run the
entire thing locally with zero API costs. Quality depends on the
model — llama-3.3 works well for routing, but local models struggle
with complex tool-use chains compared to deepseek or Claude.

**"Why not just use LangGraph directly?"**
For a 2-agent setup, LangGraph directly is fine. When you have 5
topologies, 12 archetypes, 21 skills, and 9 MCP servers — all with
different model configs, IAM scopes, and tool assignments — managing
that in code gets painful. The YAML layer is what makes it manageable.

---

# Images needed

### Image 1: cross-consultation-flow.png
Same as Reddit Image 2 — flow diagram showing parallel delegation
to architect + developer with model costs annotated. Clean,
professional, light background for LinkedIn.

### Image 2: cost-breakdown.png
Same data as Reddit Image 4 but styled for LinkedIn — cleaner,
more corporate. Horizontal bar chart with the $0.33 total
prominently displayed. Light background, professional font.
