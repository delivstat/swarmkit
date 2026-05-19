---
title: Reddit post for r/LangChain, r/LocalLLaMA, r/MachineLearning
description: Humble, simple introduction to SwarmKit — no domain-specific references
---

# Title options (pick one):

- "I got tired of writing LangGraph Python for every new agent setup, so I made the topology a YAML file"
- "Multi-agent swarms in YAML instead of Python — here's what I learned running it on a real project"
- "What if your agent topology was a config file, not code?"

---

# Post

I work on enterprise projects — the kind where you have thousands
of documents, dozens of APIs, configuration dumps, and project code
scattered across different systems. Last year I needed multi-agent
setups to make sense of all this, and kept running into the same
problem: every time I wanted to change who does what (add an agent,
swap a model, give someone a new tool), I was back in Python
rewriting LangGraph state graphs.

So I built SwarmKit. The idea is pretty simple — your agent topology
is a YAML file:

```yaml
agents:
  root:
    role: root
    model: { provider: openrouter, name: meta-llama/llama-3.3-70b-instruct }
    children:
      - id: researcher
        role: worker
        archetype: domain-researcher
      - id: analyst
        role: worker
        archetype: code-analyst
```

The runtime compiles this into a LangGraph state graph. You change the
YAML, the graph changes. No Python to touch.

[IMAGE: topology-yaml-to-graph.png]

## What it actually does in practice

I've been running this on a real enterprise project. The workspace
has 5 different agent topologies, 21 skills, and 9 MCP tool servers
(ChromaDB for docs, config parsers, API documentation, Jira,
Confluence, code search, PDF reader with vision, etc).

When someone asks "how does feature X work in our project?", the root
agent sends the question to both a researcher and a code analyst.
The researcher searches project docs, configuration, API references,
and Jira tickets. The analyst greps the source code and reads specific
lines from the relevant files. Both run in parallel. The root combines
both perspectives into one answer.

One question, two specialists, merged result. The topology YAML
defines who can delegate to whom. The runtime handles the rest.

[IMAGE: cross-consultation-flow.png]

## Things I learned the hard way

**Tool names matter more than prompts.** I had a tool called
`get-api-docs` in a code analyst's list. When users asked about
how the code builds something, the model called that tool every
time — it returns generic documentation, not the project's actual
code. No amount of "DO NOT use this tool for code questions" in the
system prompt changed the behaviour. I removed the tool from the
list. Problem gone.

The lesson: shape agent behaviour through tool availability, not
prompt instructions. If a tool name matches what the user asked,
the model will call it regardless of what you wrote in the prompt.

**Models say "let me look into that" and then stop.** After a search
returned results, the model would respond with "Let me examine the
file..." without actually calling the file reader. Just planning
language, no action. I added detection for this — if the response is
short and contains phrases like "let me" or "I'll examine", the
runtime sends it back with "you described what you plan to do but
didn't do it." Small thing, but it eliminated a whole class of
lazy non-answers.

**Raw tool output is useless for anyone who isn't a developer.**
Vector search similarity scores, truncated grep lines, JSON config
dumps — that's what agents were returning as "answers." Adding one
extra LLM call where the agent sees its own tool results and writes
a coherent response changed everything. It costs one additional model
call per turn but makes the output actually usable.

[IMAGE: before-after-synthesis.png]

**Conversation history grows fast and agents get confused.** After
4-5 turns, the context was full of raw tool outputs from previous
turns. The model would get confused, repeat old findings, or
contradict itself. Three things helped:
- Tool result caching — same search in the same conversation returns
  from cache instead of re-executing
- History compaction — only the last 3 turns stay full, older turns
  become one-line summaries
- Tool result truncation — large outputs get trimmed before entering
  context, full result stays in cache

## The cost thing

This was honestly the part that surprised me most. Each agent gets
its own model in the YAML:

- Router: llama-3.3-70b at $0.10/M tokens — just deciding who
  handles the question
- Workers: deepseek-chat at $0.32/M — doing the actual reasoning
  and tool use
- Tool calls (grep, file read, vector search, config lookup): $0,
  all local MCP servers

Over a full working day: 507 requests, 1.9M tokens, $0.33 total.
I double-checked this number because it seemed wrong. The trick is
that most of the work is tool calls that run locally for free. The
LLM only handles routing and synthesis.

[IMAGE: cost-breakdown.png]

## What's in it

- **7 model providers** — OpenRouter, Anthropic, OpenAI, Google,
  Groq, Together, Ollama. Mix and match per agent.
- **MCP tool servers** — Confluence, Jira, ChromaDB, code search,
  PDF reader with vision (Gemini Flash describes diagrams), filesystem
- **Conversational authoring** — `swarmkit init .` creates a workspace
  through conversation. `swarmkit author skill .` creates new skills.
  The workspace I run in production grew from 11 to 21 skills this way.
- **Tool result caching** — same call in the same conversation returns
  from a content-addressed cache
- **History compaction** — old turns become summaries, raw tool output
  never enters conversation history
- **Parallel delegation** — when the root sends to multiple workers,
  they run concurrently via asyncio.gather
- **Governance abstraction** — policy checks on every action (honestly,
  this part is more designed than fully implemented — the boundaries
  are real, the full judicial tiering isn't wired yet)

## What's not great yet

Being honest about the rough edges:

- **Output quality varies between runs.** Same prompt, same model,
  different tool call order. Temperature 0.3 means the model samples
  differently each time. Some runs are excellent, some miss things.
- **`swarmkit eject` doesn't exist yet.** The design says you should
  be able to export standalone LangGraph code. Not implemented.
- **No web UI.** CLI only right now. Works for developers, not great
  for everyone else.
- **Large files overwhelm the model.** A 2,000-line source file as
  a single tool response can exceed context. We added line-range
  reading but the agent doesn't always use it.
- **Models hallucinate tool results.** The agent sometimes says "I
  downloaded the file" without actually calling the download tool.
  We added verification, but it's not foolproof.

## Try it

```bash
uv tool install swarmkit-runtime
swarmkit init my-swarm/
```

Code: https://github.com/delivstat/swarmkit
MIT license. The design doc is in the repo — it's long and opinionated.

I'm genuinely looking for feedback, especially from people who've
built multi-agent systems and hit similar problems. What patterns
worked for you? What did I get wrong?
