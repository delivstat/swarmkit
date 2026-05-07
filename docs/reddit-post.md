---
title: Reddit post for r/LangChain, r/LocalLLaMA, r/MachineLearning
description: Humble, simple introduction to SwarmKit
---

# Title options (pick one):

- "I got tired of writing LangGraph Python for every new agent setup, so I made the topology a YAML file"
- "Multi-agent swarms in YAML instead of Python — here's what I learned running it on a real project"
- "What if your agent topology was a config file, not code?"

---

# Post

I work on enterprise systems — IBM Sterling, SAP, that kind of thing.
Last year I needed multi-agent setups for a project and kept running
into the same problem: every time I wanted to change who does what
(add an agent, swap a model, give someone a new tool), I was back in
Python rewriting LangGraph state graphs.

So I built SwarmKit. The idea is pretty simple — your agent topology
is a YAML file:

```yaml
agents:
  root:
    role: root
    model: { provider: openrouter, name: meta-llama/llama-3.3-70b-instruct }
    children:
      - id: architect
        role: worker
        archetype: sterling-architect
      - id: developer
        role: worker
        archetype: sterling-developer
```

The runtime compiles this into a LangGraph state graph. You change the
YAML, the graph changes. No Python to touch.

[IMAGE 1: topology-yaml-to-graph.png — side by side: YAML on left,
compiled graph visualisation on right]

## What it actually does in practice

I've been running this on a real IBM Sterling OMS implementation.
The workspace has 5 topologies, 21 skills, and 9 MCP tool servers
(ChromaDB for docs, CDT config parser, API javadocs, Jira/Confluence,
code search, etc).

When someone asks "how are sourcing rules managed?", the root agent
sends the question to both an architect and a developer. The architect
searches project docs, configuration dumps, and API references. The
developer greps the Java source and reads specific lines from the
relevant class. Both run in parallel now (just shipped that). The root
combines both perspectives into one answer.

[IMAGE 2: cross-consultation-flow.png — diagram showing:
User → Root (llama-3.3) → parallel → Architect (deepseek) + Developer (deepseek)
                                      ↓ docs/config/APIs    ↓ grep/read-file/code
                        → Root merges both → synthesised answer]

## Things I learned the hard way

**Tool names matter more than prompts.** I had a tool called
`get-api-input-xml` in the developer's list. When users asked about
XML in the project code, the model called that tool every single time
— it returns generic product docs, not the project's actual code. No
amount of "DO NOT use this tool for code questions" in the system
prompt changed the behaviour. I removed the tool from the list.
Problem gone.

**Models say "let me look into that" and then stop.** After grep
returned results, the model would respond with "Let me examine the
file..." without actually calling `read_file`. Just planning language,
no action. I added detection for this — if the response is short and
contains phrases like "let me" or "I'll examine", the runtime sends
it back with "you described what you plan to do but didn't do it."

**Raw tool output is useless for non-developers.** ChromaDB similarity
scores, truncated grep lines, JSON config dumps — that's what agents
were returning as "answers." Adding a follow-up LLM call where the
agent sees its own tool results and writes a coherent response changed
everything. It costs one extra model call per turn but makes the
output actually usable.

[IMAGE 3: before-after-synthesis.png — split screen:
Left: "Before" showing raw ChromaDB scores and grep output
Right: "After" showing a clean, structured answer with sections]

## The cost thing

This was honestly the part that surprised me most. Each agent gets
its own model in the YAML:

- Router: llama-3.3-70b at $0.10/M tokens — just deciding who
  handles the question
- Workers: deepseek-chat at $0.32/M — doing the actual reasoning
- Tool calls (grep, file read, ChromaDB, config lookup): $0, local

Over a full working day: 507 requests, 1.9M tokens, $0.33 total.
I double-checked this number because it seemed wrong.

[IMAGE 4: cost-breakdown.png — simple bar chart:
Router calls: $0.02
Worker calls: $0.28
Tool calls: $0.00
Synthesis calls: $0.03
Total: $0.33]

## What's in it

- 7 model providers (OpenRouter, Anthropic, OpenAI, Google, Groq,
  Together, Ollama)
- MCP tool servers for everything (Confluence, Jira, ChromaDB, code
  graph, PDF reader with vision, filesystem)
- Conversational authoring — `swarmkit init .` creates a workspace
  through conversation, `swarmkit author skill .` creates new skills
- Tool result caching — same call in the same conversation returns
  from cache
- History compaction — old turns become summaries, raw tool output
  never enters the conversation history
- `/clear` in chat to start fresh without restarting
- Governance abstraction (policy checks on every action, but honestly
  this is the part that's designed more than implemented — the
  boundaries are real, the full judicial tiering isn't wired yet)

## What's not great yet

Being honest about the rough edges:

- **Output quality varies between runs.** Same prompt, same model,
  different tool call order. Temperature 0.3 means the model samples
  differently each time. Some runs are excellent, some miss the mark.
- **`swarmkit eject` doesn't exist yet.** The design says you should
  be able to export standalone LangGraph code. It's not implemented.
  The constraint it imposes is real though — every feature must have
  an ejection story.
- **No web UI.** CLI only. Works fine for developers, not great for
  everyone else. v1.1 will add a visual topology editor.
- **The governance model is logically separated, not process-isolated.**
  The four pillars run in the same process. Full isolation is v2.0.
- **Large files overwhelm the model.** A 2,200-line Java file as a
  single tool response exceeds context. We added line-range reading
  but the agent doesn't always use it.

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

---

## Images to create

### Image 1: topology-yaml-to-graph.png
**Description:** Split view. Left side shows clean YAML topology
(3 agents: root, architect, developer). Right side shows the same
topology as a visual graph with boxes and arrows. Arrow in the
middle with "compile_topology()" label. Clean, minimal, dark
background with white/cyan text.

### Image 2: cross-consultation-flow.png
**Description:** Flow diagram. User query at top. Root agent (box,
labeled "llama-3.3 / $0.10/M") in the middle. Two parallel arrows
going to Architect (box, "deepseek / $0.32/M", with tool icons:
docs, config, APIs) and Developer (box, "deepseek / $0.32/M", with
tool icons: grep, read-file, code). Both arrows converge back to
Root, which outputs "Synthesised answer" at the bottom. Show the
parallel execution with a "asyncio.gather" annotation.

### Image 3: before-after-synthesis.png
**Description:** Split screen. Left side "Before" (red tint):
messy output with ChromaDB scores like "[score: 0.45]", truncated
text snippets, raw grep line numbers. Right side "After" (green
tint): clean structured answer with "### Data Flow", "### Key APIs",
"### Implementation Notes" sections. Same information, completely
different presentation.

### Image 4: cost-breakdown.png
**Description:** Simple horizontal bar chart on dark background.
Four bars:
- "Router (llama-3.3)" — tiny bar — "$0.02"
- "Workers (deepseek)" — medium bar — "$0.28"
- "Tool calls (local)" — no bar — "$0.00"
- "Synthesis" — tiny bar — "$0.03"
Total at bottom: "$0.33 / day"
Subtitle: "507 requests, 1.9M tokens"
