---
title: SwarmKit launch post
description: Announcement post for v1.0 launch. Adapt per platform.
---

# Introducing SwarmKit — multi-agent AI swarms from YAML, not code

The reason I built SwarmKit was because every multi-agent system I worked on ended up with the same problem — the coordination logic was harder to build and maintain than the agents themselves. You'd have an LLM that could review code or analyse documents perfectly well, but wiring three of them together with proper delegation, governance, and observability took weeks of custom plumbing every single time.

Most frameworks in this space are code-first. You define agents as Python classes, wire them together imperatively, and hope the coordination holds up when you add a fourth agent or swap a model. There's no way to look at the system and understand its shape without reading the code. And when something goes wrong mid-run, you're adding print statements to figure out which agent did what.

SwarmKit takes a different approach. The topology — who exists, who reports to whom, what skills they can exercise — is a YAML file. The runtime compiles it into a LangGraph state graph and handles delegation, tool execution, and synthesis automatically. You can look at a topology and understand the swarm's structure in seconds without reading any Python.

```yaml
agents:
  root:
    role: root
    model: { provider: openrouter, name: meta-llama/llama-3.3-70b-instruct }
    children:
      - id: architect
        role: worker
        archetype: sterling-architect    # deepseek-chat, temp 0.2
      - id: developer
        role: worker
        archetype: sterling-developer    # deepseek-chat, temp 0.3
```

Two workers, each with different model configurations, different tools, different expertise. The root delegates questions to the right specialist. When both are needed, it sends to both and synthesises their findings. All expressed in YAML — no orchestration code.

## What we shipped and what we learned

I'm not going to pretend this launched fully polished. We've been running SwarmKit on a real IBM Sterling OMS implementation project for the past few weeks, and the framework has been evolving fast based on what actually works and what doesn't.

The Sterling workspace has five topologies, five archetypes, twenty-one skills, and nine MCP tool servers covering everything from CDT configuration dumps to ChromaDB vector search over 17,000 product docs to structured access to 1,006 API javadocs. It's the kind of knowledge-intensive domain where a single RAG pipeline falls short — you need specialists who know where to look and how to combine what they find.

**Cross-consultation** was the feature that changed how the team uses the system. When someone asks "how are sourcing rules managed in this project?", the root sends the question to both the architect and the developer simultaneously. The architect searches project documentation, CDT configuration, and API references. The developer greps the Java source, reads specific line ranges from the relevant class, and traces the actual code flow. The root receives both and merges the design context with the implementation detail into a single answer.

**The multi-turn tool loop** was something we added after watching agents dump raw ChromaDB scores and truncated grep output as their "answer." Now when an agent calls tools, it gets the results back and writes a coherent synthesis. If it needs more data, it makes additional tool calls — up to eight rounds per turn. The model can grep for a file, read specific methods, check the API schema for XML attribute structure, and verify its own citations, all within a single agent turn.

**Per-agent model control** is what makes the economics viable. The router uses llama-3.3-70b at $0.10 per million tokens — it's doing classification, not reasoning. Workers use deepseek-chat at $0.32/$0.89. All tool calls (grep, file reads, vector search, config lookups) run locally for free. Over a full working day: 507 requests, 1.9 million tokens, $0.33 total.

## How skills work

Every capability an agent exercises is a skill, and there are four categories — capability (does something), decision (evaluates something), coordination (hands work to another agent), and persistence (writes to storage). A skill can be backed by an LLM prompt or by an MCP tool server:

```yaml
# MCP-backed capability skill
implementation:
  type: mcp_tool
  server: github
  tool: get_file_contents

# LLM-driven decision skill
implementation:
  type: llm_prompt
  prompt: "Evaluate code quality..."
```

Since there are 7,000+ public MCP servers already available (GitHub, Slack, databases, search engines, file systems), most capabilities are just a few lines of YAML pointing at an existing server. I didn't want to build another plugin system — skills are the only extension primitive, and the MCP ecosystem is the skill library.

## Conversational authoring

Users shouldn't need to write YAML to use SwarmKit. The topology-as-data approach is great for the runtime, but asking someone to author valid YAML with the right schema is still a friction point. So I built conversational authoring — `swarmkit init` creates a workspace through conversation, `swarmkit author` creates individual artifacts, and `swarmkit edit` modifies an existing workspace based on what you describe.

The authoring system has the full schema baked into its system prompt, validates output against the JSON Schema, and corrects errors automatically. It works with just a CLI install and an API key — no external knowledge servers needed.

The Sterling workspace started with eleven skills and has grown to twenty-one, with most of that growth coming through the authoring flow. We'd notice a gap during real use — the agents couldn't read specific line ranges from large files, or they had no way to verify that code citations matched the actual source — and we'd author a new skill to fill it through the same conversational interface.

## Governance

I've worked on enough enterprise systems to know that governance can't be bolted on after the fact. SwarmKit implements a separation of powers model with four distinct pillars:

The **legislative** pillar is your topology YAML, IAM policies, and scope definitions — written by humans, immutable at runtime. Agents cannot modify them.

The **executive** pillar is every agent in the system, operating within the bounds the legislative layer defined. An agent cannot grant itself new scopes, modify its own topology, disable its evaluators, or suppress its audit trail.

The **judicial** pillar evaluates executive actions through a three-tier system designed to keep costs sane. Tier 1 is AGT's deterministic policy engine — schema checks, IAM scope validation, prompt injection detection — at sub-millisecond latency with zero LLM tokens. Every action passes through this. Tier 2 is a single LLM judge for semantic evaluation when needed. Tier 3 is a multi-persona panel for high-stakes decisions, with human-in-the-loop escalation on panel disagreement. The target is 10-20% governance overhead, not the 300-400% you'd get from running every output through an LLM judge.

The **media** pillar is the append-only audit log, observability layer, and review queues. Agents cannot modify past entries — even attempted suppression gets recorded.

The governance engine is AGT (Agent Governance Toolkit), wrapped behind a `GovernanceProvider` abstraction with four methods. AGT is the v1.0 implementation, but if something better emerges, a new provider replaces it without touching the topology schema or runtime. Same Terraform-for-cloud-providers pattern.

I should be honest about what's implemented versus designed: the four pillars run as logically separated modules in a single process today. Full process-level isolation is a v2.0 target. The automatic judicial tier escalation (Tier 2 low confidence → auto-route to Tier 3 panel) is designed but not yet wired end-to-end. The governance boundaries are real — every agent execution and MCP tool call goes through `evaluate_action` — but the full vision isn't complete yet.

For development you set `governance: { provider: mock }` and everything is permitted. For production you switch to `governance: { provider: agt }` and the policy engine enforces scope checks, identity verification, and trust scoring. Same topology, same skills, different governance posture.

## Seven model providers

SwarmKit doesn't lock you into a single LLM provider. The runtime ships with seven built-in providers (Anthropic, OpenAI, Google, OpenRouter, Groq, Together, Ollama), and each agent can use a different one. Switching a provider is one line in your archetype YAML.

This means you can run your root agent on Groq for fast routing, your workers on OpenRouter for model variety, and your validation agents on a local Ollama instance for zero-cost evaluation — all in the same topology. In the Sterling workspace we mix llama-3.3 for routing with deepseek-chat for workers, but we've tested the same topology with Ollama for fully offline operation and with Anthropic's Claude directly for complex reasoning tasks.

The `/model` command in chat mode lets you switch at runtime: `/model google/gemini-2.5-flash` swaps the active model, `/model reset` returns to the topology defaults. Useful for comparing quality and cost on the same query without restarting.

## Observability

Every run records structured events — agent start/complete with timing, skill calls, policy denials, validation failures — to `.swarmkit/logs/` as JSONL. The CLI gives you several ways to inspect what happened:

```bash
swarmkit run . my-topology --input "..." --verbose
swarmkit status .                   # recent runs at a glance
swarmkit logs .                     # detailed events
swarmkit why <run-id> .             # LLM explains what happened
swarmkit ask "which agent was slow?" -w .   # conversational observer
```

The verbose mode shows the full agent flow in real time — which model is called, what tools are invoked, MCP argument values, tool loop turns, and synthesis calls.

## What's next

The web UI is the main v1.1 item — a visual topology editor and runtime dashboard over the HTTP server. `swarmkit eject` (exporting standalone LangGraph code from a topology) is designed but not yet implemented — the architectural constraint it imposes is real though, since every runtime feature must have an ejection story.

I'm also working on a content creation workspace for [rynko.dev](https://rynko.dev) with automated blog and LinkedIn post generation using the same multi-agent pattern, and a trade document intelligence workspace for [klervex.com](https://klervex.com).

## Try it

```bash
pip install swarmkit-runtime
swarmkit init my-swarm/
```

The code is at [github.com/delivstat/swarmkit](https://github.com/delivstat/swarmkit). MIT license. The full architecture is documented in `design/SwarmKit-Design-v0.6.md` — it's detailed and opinionated, and I'd appreciate feedback on it.

---

## Platform-specific versions

### Reddit (r/LangChain or r/LocalLLaMA)

**Title:** I built a framework where multi-agent swarms are YAML files, not Python. Cross-consultation, synthesis, and self-authoring — 500 requests for $0.33.

I've been building enterprise systems for 20+ years, and last year I started building SwarmKit because I was tired of wiring LangGraph Python every time I needed a new agent topology. The core idea is that your agent graph, skills, prompts, and model selection are all YAML config files that the runtime compiles into LangGraph state graphs.

**Cross-consultation is the part that changed how we work.** When someone asks "how are sourcing rules managed in our project?", the root agent delegates to both the architect and the developer in the same turn. The architect searches 17K product docs, CDT configuration, and API references simultaneously. The developer greps the Java source, reads specific line ranges, and traces the actual XML construction code. The root synthesises both views into a single answer that has the design context from one side and the implementation detail from the other.

**Synthesis turned raw tool dumps into real answers.** Before we added the follow-up LLM call after tool execution, agents would return ChromaDB similarity scores and truncated grep output directly. Now the model sees its own tool results and writes a coherent response, making additional tool calls if needed — up to eight rounds per turn.

**Per-agent model selection is what makes the cost work.** The router uses llama-3.3-70b at $0.10/M (classification), workers use deepseek-chat at $0.32/$0.89/M (reasoning), and all tool calls run locally for free. 507 requests, 1.9M tokens, $0.33 total on a production codebase.

**The framework authors itself.** `swarmkit author skill .` starts a conversation where the system helps create new capabilities, validates against JSON Schema, and corrects errors automatically. The Sterling workspace grew from 11 to 21 skills through this flow.

One lesson I keep relearning: tool names drive model behaviour more than prompt instructions. I had `get-api-input-xml` in the developer's tool list, and when users asked about XML in the project code, the model called that tool every time regardless of the prompt. Removing the tool fixed it immediately.

Governance uses AGT (Agent Governance Toolkit) behind a GovernanceProvider abstraction — separation of powers model with deterministic policy checks (sub-ms, $0) on every action, LLM judges only when semantic evaluation is needed, and human-in-the-loop for high-stakes decisions. The four pillars (legislative, executive, judicial, media) run as logically separated modules today, with full process isolation planned for v2.0.

Open source: github.com/delivstat/swarmkit. The design doc is in the repo.

### LinkedIn

A developer on my team asked our AI assistant how sourcing rules work in our Sterling OMS project last week, and the way the system handled it is what convinced me we're on the right track with SwarmKit.

The system didn't pass the question to a single agent. It recognised that this touches both design and implementation, so it routed to two specialists at the same time. The architect searched 17,000 product docs, the CDT configuration dump, and 1,006 API references in a single call, and came back with the integration pattern: SAP feeds sourcing rules to OMS via a JMS queue, OMS pushes them to the Inventory Cache microservice, and the rules define which ship nodes can serve which pincodes for each fulfillment type. Meanwhile, the developer grepped the Java source, found `SourcingRuleFileUploadAgent.java`, read lines 2080-2216 using a line-range read tool, and traced the actual `createElement` and `setAttribute` calls that build the XML input for the `manageSourcingRule` API.

The root agent received both sets of findings and synthesised them into a single answer — the architectural view from one side and the actual code with line references from the other. That's cross-consultation: not "agent A passes output to agent B" in a chain, but two agents working the same problem from different perspectives while a coordinator merges the views.

This is SwarmKit, an open-source framework I've been building where multi-agent AI systems are defined in YAML config files instead of Python code. The runtime compiles topology files into executable LangGraph graphs and handles the routing, tool execution, and synthesis automatically.

Three things surprised me while building it.

First, per-agent model control changes the economics completely. The router uses llama-3.3 at $0.10 per million tokens because it's really just doing classification — deciding which specialist gets each question. The workers use deepseek-chat at $0.32 for the actual reasoning and code analysis, and all the tool calls like grep, file reads, and vector search run locally for free. Over a full working day on a production project, that added up to 507 requests and 1.9 million tokens for $0.33 total.

Second, tool names drive model behaviour more than prompt instructions. I had `get-api-input-xml` in the developer agent's tool list, and when users asked about XML structures in the project code, the model would call that tool every time because the name matched the query so well. It returns generic product documentation, not what the actual code builds, and no amount of "DO NOT use this tool for code questions" in the system prompt changed the behaviour. Removing the tool from the agent's list fixed it immediately, and that taught me to shape agent behaviour through tool availability rather than prompt engineering.

Third, the framework can author itself. Running `swarmkit author skill .` starts a conversation where the system helps you create new capabilities, validates the output against the JSON Schema, and corrects any errors automatically. The Sterling workspace started with 11 skills and has grown to 21, with most of the new skills coming from gaps we noticed during real use — like the agents not being able to read specific line ranges from large files, or not having a way to verify that code citations actually match the source.

The governance layer uses AGT (Agent Governance Toolkit) behind a narrow abstraction — four methods covering policy evaluation, identity verification, event recording, and trust scoring. Deterministic policy checks run on every action at sub-millisecond latency and zero LLM cost. LLM judges only fire when semantic evaluation is actually needed. The target is 10-20% governance overhead, not the 300-400% you'd get from judging everything. I should be honest that the full judicial tier automation is designed but not yet wired end-to-end — the boundaries are real, the auto-escalation isn't.

I'm still working through something I don't have a clean answer to. We built a deterministic verifier that checks every `file.java:line` citation against the actual source file, and it catches hallucinated line numbers reliably at zero cost since it's just a Python script reading files. But it can't tell you whether the model's interpretation of that code is correct — the line is real, the code is real, but the explanation of what it does might still be wrong. How do you close that gap without putting a human reviewer on every response?

github.com/delivstat/swarmkit

### Twitter/X thread

**1/** I built SwarmKit — an open-source framework where multi-agent AI swarms are YAML files, not Python code. Your topology declares agents, hierarchy, skills, and governance. The runtime compiles it into LangGraph and handles delegation, tool loops, and synthesis. v1.0 is live.

github.com/delivstat/swarmkit

**2/** The core feature is cross-consultation. Ask "how does X work?" → root sends to both architect (searches docs, config, APIs) and developer (greps code, reads specific lines). Root synthesises both views into one answer. Design context + implementation detail, merged.

**3/** Every agent capability is a skill backed by an LLM prompt or MCP server. 7,000+ community MCP servers work as skills out of the box. GitHub, Slack, databases, search — just point a skill at the server.

**4/** Per-agent model control is what makes the cost work. Router: llama-3.3 at $0.10/M. Workers: deepseek-chat at $0.32/M. Tool calls: $0 (local). Full day on production Sterling OMS project: 507 requests, 1.9M tokens, $0.33.

**5/** Governance isn't bolted on — it's structural. AGT-backed separation of powers. Deterministic policy checks (sub-ms, $0) on every action. LLM judges only when semantic evaluation needed. 10-20% overhead target, not 300%.

**6/** The framework authors itself. `swarmkit author skill .` → conversation → validated YAML. Workspace grew from 11 to 21 skills through this flow. 7 model providers. MIT license. pip install swarmkit-runtime.
