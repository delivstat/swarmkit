---
title: SwarmKit launch post
description: Announcement post for v1.0 launch. Adapt per platform.
---

# SwarmKit v1.0 — compose multi-agent AI swarms from YAML, not code

We just shipped SwarmKit — an open-source framework for composing,
running, and growing multi-agent AI swarms. The core idea: swarm
topology (who exists, who reports to whom, what skills they have)
is declarative YAML, not imperative Python.

## The problem

Building multi-agent systems today means writing a lot of plumbing
code — agent loops, tool dispatch, model routing, state management,
coordination protocols. Every team reinvents this. And when the
system breaks, there's no observability into what each agent did
or why.

## What SwarmKit does differently

**1. Topology as data.** Your swarm is a YAML file:

```yaml
agents:
  root:
    role: root
    archetype: supervisor-leader
    children:
      - id: code-reviewer
        role: worker
        archetype: code-analyst
      - id: security-reviewer
        role: worker
        archetype: security-reviewer
```

No Python classes to write. The runtime interprets the topology
and compiles it to a LangGraph execution graph.

**2. Skills as the only extension primitive.** Every capability an
agent exercises is a skill — backed by an LLM prompt or an MCP
tool server. Four categories: capability, decision, coordination,
persistence.

**3. Conversational authoring.** Users create workspaces through
conversation (`swarmkit init`), never writing YAML directly. A
multi-agent authoring swarm handles schema drafting, validation,
test execution, and file publication.

**4. Governance built in.** AGT-backed policy enforcement, identity
verification, hash-chained audit. Every MCP tool call goes through
`evaluate_action` before execution.

**5. MCP integration.** Any MCP server becomes a skill. 7,000+
community servers available. Docker sandbox isolation for generated
servers.

**6. Observability.** Every run records per-agent timing, skill
calls, policy denials. `swarmkit why` asks an LLM to explain what
happened. `swarmkit ask` is a conversational observer.

## What ships in v1.0

- **CLI:** `validate`, `run`, `init`, `author`, `edit`, `serve`,
  `status`, `logs`, `why`, `ask`, `knowledge-pack`, `knowledge-server`
- **HTTP server** — FastAPI wrapping the same runtime the CLI uses
- **7 model providers** — Anthropic, Google, OpenAI, OpenRouter,
  Groq, Together, Ollama (auto-detected from env vars)
- **2 reference topologies:**
  - Code Review Swarm (3 leaders, 10 agents, reviews real GitHub PRs)
  - Skill Authoring Swarm (6 agents, creates/edits workspaces)
- **Knowledge MCP Server** — 11 tools for live docs search
- **20 reference skills, 16 archetypes**
- **Docker image + PyPI packages**
- **500+ tests** across Python + TypeScript

## 5-minute demo

```bash
pip install swarmkit-runtime
swarmkit init my-swarm/
# Answer a few questions → working workspace
swarmkit run my-swarm/ my-topology --input "Do the thing" --verbose
```

Or run the Code Review Swarm against a real PR:

```bash
export OPENROUTER_API_KEY=sk-or-...
export GITHUB_TOKEN=ghp_...
swarmkit run reference/ code-review \
  --input "Review PR #49 on delivstat/swarmkit"
```

## Why we built it

We needed multi-agent systems for production use cases (code
review, solution architecture, knowledge management) and found
that:

1. Existing frameworks are code-first — non-developers can't
   compose agent teams
2. No framework treats governance as structural (not bolted on)
3. MCP integration is tool-level, not topology-level
4. Observability is always an afterthought

SwarmKit is our answer: topology-as-data lets anyone compose,
governance is built into the execution model, MCP servers are
first-class skills, and every run is observable.

## Links

- **GitHub:** https://github.com/delivstat/swarmkit
- **PyPI:** `pip install swarmkit-runtime`
- **Docker:** `docker pull ghcr.io/delivstat/swarmkit`
- **Docs:** https://delivstat.github.io/swarmkit/
- **Design doc:** The full v0.6 architecture is in the repo at
  `design/SwarmKit-Design-v0.6.md`

## What's next

- Web UI (v1.1) — visual topology editor over the HTTP server
- Trigger support — cron/webhook/file_watch execution
- Interactive chat mode for `swarmkit edit`
- More reference topologies (Skill Authoring, Knowledge Curator)

Feedback, issues, and contributions welcome at
https://github.com/delivstat/swarmkit/issues

---

## Platform-specific versions

### Twitter/X (thread)

**Tweet 1:**
SwarmKit v1.0 is live — an open-source framework for multi-agent
AI swarms where topology is YAML, not code.

Your swarm = a file. The runtime handles compilation, governance,
MCP integration, and observability.

pip install swarmkit-runtime
github.com/delivstat/swarmkit

🧵

**Tweet 2:**
What makes it different:
• Topology as data (YAML, not Python classes)
• Skills = only extension primitive (LLM prompts or MCP tools)
• Conversational authoring (swarmkit init, never write YAML)
• AGT governance built in (policy enforcement on every action)
• 7 model providers auto-detected from env

**Tweet 3:**
Ships with a Code Review Swarm — 3 leaders (Engineering, QA, Ops),
10 agents, reviews real GitHub PRs via MCP. Knowledge-grounded:
agents search the design docs before producing verdicts.

swarmkit run reference/ code-review --input "Review PR #49"

**Tweet 4:**
Full observability:
• swarmkit run --verbose (per-agent timing)
• swarmkit logs (event history)
• swarmkit why (LLM explains what happened)
• swarmkit ask "which agent is slowest?"

Every run saves structured events as JSONL.

**Tweet 5:**
Cost-optimized: Qwen3 235B for leaders ($0.46/M), Qwen3 30B for
workers ($0.08/M). A full topology run costs ~$0.01-0.02. 20
runs/day = ~$5/month.

7 providers: Anthropic, Google, OpenAI, OpenRouter, Groq, Together,
Ollama (local, free).

**Tweet 6:**
Open source. MIT license. 500+ tests. Design doc included.

GitHub: github.com/delivstat/swarmkit
PyPI: pip install swarmkit-runtime
Docker: ghcr.io/delivstat/swarmkit

Feedback welcome. What would you build with it?

---

### LinkedIn

**Title:** Introducing SwarmKit — multi-agent AI swarms from YAML, not code

[Use the main post content above, formatted for LinkedIn's longer
form. Add a personal angle about why you built it and what problem
it solves for your team.]

---

### Hacker News

**Title:** Show HN: SwarmKit – Compose multi-agent AI swarms from YAML (open source)

**Body:** [Use the main post content, stripped to essentials. HN
prefers technical substance over marketing. Lead with the
architecture decision (topology-as-data) and the GitHub link.]

---

### Reddit (r/MachineLearning, r/LocalLLaMA, r/artificial)

**Title:** [P] SwarmKit v1.0 — open-source framework for
multi-agent AI swarms (topology-as-data, MCP integration, 7
providers including Ollama)

**Body:** [Use the main post. r/LocalLLaMA will care about Ollama
support and the Qwen/DeepSeek pricing. r/MachineLearning will care
about the architecture.]

---

### Dev.to / Hashnode

**Title:** Building Multi-Agent AI Swarms Without Writing Code — Introducing SwarmKit

[Full blog post format. Include code samples, architecture
diagrams (from the topology tree output), and the 5-minute demo.]
