---
title: Swael launch post
description: Announcement post for v1.0 launch. Adapt per platform.
---

# Introducing Swael — multi-agent AI swarms from YAML, not code

The reason I built Swael was because every multi-agent system I worked on ended up with the same problem — the coordination logic was harder to build and maintain than the agents themselves. You'd have an LLM that could review code or analyse documents perfectly well, but wiring three of them together with proper delegation, governance, and observability took weeks of custom plumbing every single time.

Most frameworks in this space are code-first. You define agents as Python classes, wire them together imperatively, and hope the coordination holds up when you add a fourth agent or swap a model. There's no way to look at the system and understand its shape without reading the code. And when something goes wrong mid-run, you're adding print statements to figure out which agent did what.

Swael takes a different approach. The topology — who exists, who reports to whom, what skills they can exercise — is a YAML file. The runtime interprets it. You can look at a topology and understand the swarm's structure in seconds without reading any Python. Here's what the Code Review Swarm looks like:

```yaml
agents:
  root:
    role: root
    archetype: supervisor-leader
    children:
      - id: engineering-leader
        role: leader
        archetype: engineering-leader
        children:
          - id: code-reviewer
            role: worker
            archetype: code-analyst
          - id: security-reviewer
            role: worker
            archetype: security-reviewer
      - id: qa-leader
        role: leader
        archetype: qa-leader
      - id: ops-leader
        role: leader
        archetype: ops-leader
```

Three leaders (Engineering, QA, Operations), each with specialist workers underneath. The root supervisor delegates sequentially — engineering reviews first, then QA assesses test coverage based on the engineering findings, then operations evaluates deployment risk. If the ops leader's confidence is low, the result lands in a human review queue. All of this is expressed in the YAML above plus the archetype definitions — no orchestration code to write.

## How skills work

Every capability an agent exercises is a skill. There are four categories — capability (does something), decision (evaluates something), coordination (hands work to another agent), and persistence (writes to storage). A skill can be backed by an LLM prompt or by an MCP tool server:

```yaml
# LLM-driven decision skill
implementation:
  type: llm_prompt
  prompt: "Evaluate code quality..."

# MCP-backed capability skill
implementation:
  type: mcp_tool
  server: github
  tool: get_file_contents
```

The Code Review Swarm uses GitHub MCP to fetch PR data, then LLM decision skills to evaluate quality, security, test coverage, and deployment risk. Each skill produces a structured verdict with a confidence score. The runtime handles the plumbing — tool schema forwarding, governance checks, structured output validation, auto-correction on failure.

I didn't want to build another tool registry or plugin system. Skills are the only extension primitive — if you want an agent to do something new, you write a skill YAML, not a Python class. And since there are 7,000+ public MCP servers already available (GitHub, Slack, databases, search engines, file systems), most capabilities are just a few lines of YAML pointing at an existing server.

## Conversational authoring

One thing I felt strongly about was that users shouldn't need to write YAML to use Swael. The topology-as-data approach is great for the runtime, but asking someone to author valid YAML with the right schema is still a friction point. So I built conversational authoring — `swael init` creates a workspace through conversation, `swael author` creates individual artifacts, and `swael edit` modifies an existing workspace based on what you describe.

The authoring is itself a multi-agent swarm (the Skill Authoring Swarm) — a conversation leader understands your intent, a knowledge searcher checks what already exists, a schema drafter generates the YAML, a validator checks it against the schemas and design invariants, a test writer generates smoke tests, and a publisher writes the files. Each agent has one job. An AI judge validates every output before it reaches your workspace.

## Governance

I've worked on enough enterprise systems to know that governance can't be bolted on after the fact. In Swael, every MCP tool call goes through `evaluate_action` before execution. Agents have IAM scopes — a worker with `repo:read` can't call a tool that requires `deploy:prod`. Policy rules are YAML files in a `policies/` directory. The audit log is append-only with hash-chained entries for tamper evidence.

For development you set `governance: { provider: mock }` and everything is permitted. For production you switch to `governance: { provider: agt }` and the AGT policy engine enforces scope checks, identity verification, and trust scoring. Same topology, same skills, different governance posture.

## Observability

Every run records structured events — agent start/complete with timing, skill calls, policy denials, validation failures. These save to `.swael/logs/` as JSONL automatically. The CLI gives you several ways to inspect runs:

```bash
swael run . my-topology --input "..." --verbose
# Shows per-agent timing after output

swael status .
# Recent runs at a glance

swael logs .
# Detailed events from past runs

swael why hello-20260426T134042 .
# LLM explains what happened in plain English

swael ask "which agent took the longest?" -w .
# Conversational observer
```

I wanted the "what happened?" question to be answerable in seconds, not hours of log-diving.

## Enterprise trust features

Three features that enterprise teams care about but most AI frameworks skip:

**Dry run.** `swael run --dry-run` shows the resolved agent tree, skill bindings, and MCP server connections without hitting any LLM or MCP server. A skeptical DevOps lead can see exactly what would happen before permitting execution. No tokens consumed, no side effects.

**Audit export.** `swael logs --format markdown` produces a compliance-ready report with agent performance tables, policy denials, validation failures, and a full event timeline. Paste it into your compliance ticket.

**Custom metadata.** Workspaces support `metadata.annotations` — a key-value map the runtime ignores but your enterprise systems can use (cost_center, team, environment, compliance tags). The framework stays strict on structural fields while allowing enterprise-specific extension.

## What ships in v1.0

The framework runs end-to-end today — CLI, HTTP server (`swael serve`), 7 model providers (Anthropic, Google, OpenAI, OpenRouter, Groq, Together, Ollama), MCP integration with Docker sandbox isolation, a Knowledge MCP Server with 11 tools for live documentation search, two reference topologies (Code Review Swarm and Skill Authoring Swarm), 20 reference skills across all four categories, 16 reusable archetypes, and 500+ tests.

If it helps to think in org-chart terms, here's how the agent hierarchy maps to a corporate structure:

| Role | Framework component | The corporate reality |
|---|---|---|
| Individual contributor | MCP skill / worker agent | "Just give me the PR data, I don't ask questions." |
| Quality assurance | artifact-validator | "This doesn't meet the spec. Back to the drawing board." |
| Middle management | engineering-leader | "I'm summarizing what the worker did for the executive." |
| The executive | root agent | Takes 3x longer to "synthesise" than anyone actually doing the work. |
| Internal audit | swael why | "We noticed a 3.2x latency overhead in the executive suite." |

The hierarchy isn't an accident — it's the safety rail. Because the validator is separate from the drafter, an LLM hallucination in code generation gets caught by a peer before it touches your filesystem. It's a distributed check-and-balance system.

On the cost side, running the Code Review Swarm with Qwen3 models via OpenRouter costs about $0.01-0.02 per run. Twenty runs a day works out to roughly $5/month. You can use any provider — the topology YAML sets per-agent models, so leaders can use a large reasoning model while workers use a cheap one for tool calling.

## What's next

The web UI is the main v1.1 item — a visual topology editor and runtime dashboard over the HTTP server. Interactive chat mode for `swael edit` is another priority, along with trigger support (cron, webhooks, file watches) for scheduled execution.

I'm also working on a Sterling OMS workspace as a real-world test case — a solution architect agent backed by a knowledge base of project configuration and product documentation. If that works well it'll become a guide for domain-specific agent workspaces.

## Try it

```bash
pip install swael-runtime
swael init my-swarm/
```

The code is at [github.com/delivstat/swael](https://github.com/delivstat/swael). MIT license. The full architecture is documented in `design/Swael-Design-v0.6.md` in the repo — it's detailed and opinionated, and I'd appreciate feedback on it.

If you build something with Swael, I'd love to hear about it. Issues and discussions are open on GitHub.

---

## Where to publish

| Platform | Priority | Angle |
|---|---|---|
| **Hacker News** (Show HN) | 1 | Technical architecture, link to GitHub |
| **Twitter/X** | 1 | Thread — 5-6 tweets pulling key points from above |
| **Reddit r/LocalLLaMA** | 2 | Ollama support, Qwen/DeepSeek pricing, run locally |
| **Reddit r/MachineLearning** | 2 | [P] tag, architecture decisions |
| **LinkedIn** | 2 | Professional angle, enterprise governance story |
| **Dev.to** | 3 | Full blog post (this content as-is works) |
| **Reddit r/artificial** | 3 | General AI audience |
| **Reddit r/LangChain** | 3 | LangGraph-based, comparison angle |
| **GitHub Discussions** | 3 | Announcement thread on the repo |

### Twitter thread version

**1/** I built Swael — an open-source framework where multi-agent AI swarms are YAML files, not Python code. Your topology declares agents, hierarchy, skills, and governance. The runtime handles compilation, tool dispatch, and observability. v1.0 just shipped.

github.com/delivstat/swael

**2/** The core idea: if you can describe who reports to whom and what each agent can do, you shouldn't need to write orchestration code. A 10-agent Code Review Swarm with 3 leaders, MCP integration, and governance is ~30 lines of YAML.

**3/** Every agent capability is a skill backed by either an LLM prompt or an MCP server. 7,000+ community MCP servers work as skills out of the box. GitHub, Slack, databases, search — just point a skill at the server.

**4/** Observability is built in, not bolted on. Every run records per-agent timing, skill calls, and policy denials. `swael why` asks an LLM to explain what happened. `swael ask` answers questions about your workspace.

**5/** Runs cost ~$0.01-0.02 with Qwen3 via OpenRouter. 7 providers supported including Ollama (free, local). Per-agent model selection — big model for leaders, small one for workers.

**6/** MIT license. 500+ tests. pip install swael-runtime.

### Hacker News version

**Title:** Show HN: Swael – Multi-agent AI swarms from YAML, not code

**Body:** Use the first 3 paragraphs of the main post above (the problem statement + topology-as-data approach), then link to GitHub. HN prefers you let the README speak for itself.
