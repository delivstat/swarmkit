---
title: "SwarmKit: multi-agent AI swarms as YAML, not code"
description: How we built a framework for composing multi-agent systems declaratively, and what we learned running it on a real enterprise project.
author: Srijith Kartha
date: 2026-05-03
---

# SwarmKit: multi-agent AI swarms as YAML, not code

I've spent 20+ years building enterprise systems — IBM Sterling, SAP integrations, complex orchestration layers where the core challenge was always the same: getting different components to work together without drowning in glue code.

Last year I started building multi-agent AI systems for a real project, and I hit that same wall. LangGraph is powerful, but every time I needed a new agent topology — three agents instead of two, a different routing pattern, one more tool — I was back in Python, wiring state graphs by hand. The topology was trapped in code. I couldn't share it, version it independently, or let a non-developer on the team adjust it.

So I built SwarmKit. The core idea is that multi-agent swarms should be data, not code. You define your agents, their relationships, skills, and model configuration in YAML files, and the runtime compiles them into executable LangGraph graphs. The topology is portable, version-controllable, and editable in any text editor.

This post walks through how it works, what it costs, and what we've learned running it on a real production project.

## The three ideas behind SwarmKit

SwarmKit rests on three principles that guide every architectural decision.

**Topology is data.** Swarms are YAML files the runtime interprets. Not Python classes, not generated code, not notebook cells. This means your topology is portable (share it as a gist), version-controllable (diff it in a PR), and inspectable (read it without running it). The design doc frames it this way: SwarmKit is to LangGraph what Terraform is to cloud APIs — a declarative layer that makes the underlying capability accessible to a wider audience while staying transparent for those who want to drop down.

**Skills are the only extension primitive.** Every capability an agent can exercise — calling an API, evaluating code quality, handing off to a peer, writing to a knowledge base — is modelled as a skill. There are four categories: capability (do something), decision (evaluate something), coordination (hand off to someone), and persistence (remember something). One mental model, one contribution surface. When you're tempted to add a new extension mechanism, the answer is always "how would this be a skill?"

**Swarms grow through human-approved authoring.** Swarms observe their own capability gaps. When a worker produces low-confidence output or a leader receives a task it can't delegate, the runtime logs it as a skill gap. The authoring system — itself a SwarmKit topology — turns natural-language descriptions into tested, validated skills. Humans approve every step. Evolution is safe, auditable, and reversible.

## How LangGraph fits in

SwarmKit doesn't replace LangGraph — it sits on top of it. The `compile_topology()` function in the runtime transforms your YAML topology into a LangGraph `StateGraph`. Here's what that means concretely.

Each agent in your topology becomes a LangGraph node. The root agent connects to `START`. Child agents are reachable through conditional edges. When the runtime builds the graph, it walks your agent tree, creates an async node function for each agent, and wires up the routing edges.

```python
graph: StateGraph = StateGraph(SwarmState)
agents = _collect_agents(topology.root)

for agent in agents.values():
    node_fn = _build_agent_node(agent, provider, governance, agents, mcp_manager)
    graph.add_node(agent.id, node_fn)

graph.add_edge(START, topology.root.id)
_add_routing_edges(graph, topology.root, agents)

return graph.compile()
```

The delegation mechanism is where it gets interesting. SwarmKit creates synthetic tools for each agent's children — `delegate_to_architect`, `delegate_to_developer`, and so on. When the model calls one of these tools, the runtime intercepts it, updates the graph state with `current_agent = "developer"`, and LangGraph's conditional edge routes execution to that node. The parent doesn't need to know the child's internal logic, tool list, or model configuration. It just delegates.

The graph state (`SwarmState`) carries the conversation through the graph: the user's input, which agent runs next, accumulated results from each agent, conversation history, and the final output. When an agent finishes without delegating further, execution flows back to the root. When the root produces a final text response with no more delegation calls, the graph routes to `END`.

### What happens inside each node

Each node function does several things in sequence. First, it checks governance — does this agent have permission to run? The runtime calls `evaluate_action()` through the `GovernanceProvider` interface, checking IAM scopes and trust scores. If denied, the node returns immediately with an audit log entry.

If governance passes, the node builds the system prompt from the archetype's YAML (persona, expertise, tool instructions), constructs the message list (conversation history so the agent sees what happened in prior turns), and makes the LLM call through the `ModelProvider` abstraction.

The response is then processed through a multi-turn tool loop. If the model made tool calls, the runtime executes them, feeds the results back to the model, and continues until the model produces a final text response — up to eight rounds. This means an agent can grep for a file, read specific lines, check the API schema, and synthesise an answer, all within a single agent turn.

If the model responds with planning language ("let me examine the file...") instead of actually calling tools, the runtime detects this and nudges it to act — a small addition that eliminated an entire class of lazy non-answers.

## Seven model providers, one YAML field

SwarmKit doesn't lock you into a single LLM provider. The runtime ships with seven built-in providers (Anthropic, OpenAI, Google, OpenRouter, Groq, Together, Ollama), and each agent in your topology can use a different one.

The `ModelProvider` abstraction works the same way as the `GovernanceProvider` — a narrow interface that each provider implements, with a registry that resolves the right provider per agent at runtime. Your topology doesn't know or care which provider serves a particular agent.

This means you can run your root agent on Groq for fast routing, your workers on OpenRouter for model variety, and your validation agents on a local Ollama instance for zero-cost evaluation — all in the same topology, all configured in YAML. In the Sterling workspace, we mix llama-3.3 (via OpenRouter) for routing with deepseek-chat (via OpenRouter) for workers, but we've tested the same topology with Ollama-hosted models for fully offline operation and with Anthropic's Claude directly for complex reasoning tasks.

The `/model` command in chat mode lets you switch at runtime without restarting: `/model google/gemini-2.5-flash` swaps the active model for all agents, `/model reset` returns to the topology defaults. Useful for comparing quality and cost across providers on the same query.

## Governance: why agents need separation of powers

Most multi-agent frameworks treat governance as an afterthought — add some guardrails after the agents are working, bolt on logging when something goes wrong in production. SwarmKit takes a different position: governance is structural, not decorative. The framework implements a four-pillar separation of powers model inspired by how real institutions prevent concentrated authority.

The **legislative pillar** is your topology YAML files, IAM policies, and scope definitions. These are static artifacts written by humans, loaded at runtime. Agents cannot modify them. When you define that the developer agent has `base_scope: [knowledge:read, repo:read]`, that's a legislative act — the agent can't grant itself `repo:write` at runtime, regardless of what the user asks it to do.

The **executive pillar** is every agent in the system — roots, leaders, workers. They do the actual work: calling tools, searching documents, analysing code, synthesising answers. But they operate within bounds that the legislative layer defined, and they can't change those bounds. An agent cannot modify its own topology, disable its evaluators, or suppress its audit trail.

The **judicial pillar** evaluates executive actions. This includes validation gates, LLM judges, schema checks, and business rule validators. The critical property is independence — workers cannot influence the judges evaluating their output, and leaders cannot disable evaluation for their subordinates. Judicial decisions are recorded to the media pillar, and the executive cannot suppress those records.

The **media pillar** is the audit log, observability layer, review queues, and skill gap logs. It's append-only from the executive's perspective. Agents cannot modify past entries. Even if an agent attempts to suppress a record, the suppression attempt itself gets logged.

### AGT: the governance engine

SwarmKit doesn't implement governance primitives from scratch. It uses AGT (Agent Governance Toolkit), a purpose-built governance infrastructure that provides the heavy lifting.

AGT's **Agent OS** is the policy engine that handles Tier 1 deterministic evaluation — schema validation, IAM scope checks, rate limits, capability boundaries, and prompt injection detection. It runs at sub-millisecond p99 latency and consumes zero LLM tokens. Every action in the system flows through this tier before anything else happens.

AGT's **Agent Mesh** provides cryptographic identity (Ed25519 keypairs and DIDs per agent), mutual authentication between agents, and continuous trust scoring on a 0-1000 scale. An agent that operates cleanly sits in the 800-1000 range. An agent that exhibits anomalies — repeated policy denials, low-confidence outputs, attempted scope escalation — decays toward lower tiers. SwarmKit normalises this to 0.0-1.0 and uses it for dynamic escalation.

AGT's **Agent SRE** handles telemetry — OpenTelemetry tracing, append-only audit logging via FlightRecorder (hash-chained for tamper evidence), and SLO tracking.

What SwarmKit adds on top of AGT is the swarm-specific layer: topology-as-data composition, skill and archetype abstractions, hierarchical agent instantiation, skill gap detection, judicial tiering orchestration, and review queue surfacing.

### GovernanceProvider: portability through abstraction

To avoid coupling to any single governance toolkit, SwarmKit wraps everything behind a deliberately narrow interface — four methods covering policy evaluation, identity verification, event recording, and trust scoring. AGT is the v1.0 implementation, but if something better emerges, a new provider replaces it without changes to the topology schema, runtime, or user experience. It's the same strategy Terraform uses for cloud providers.

### Judicial tiering: keeping governance costs sane

The naive approach to AI governance — run every output through an LLM judge — would multiply your token costs by 300-400%. SwarmKit avoids this through three-tier judicial routing.

**Tier 1 is deterministic and free.** AGT's policy engine handles schema conformance, IAM scope validation, rate limits, and prompt injection detection at sub-millisecond latency with zero LLM tokens. Every action passes through this tier. Most routine operations clear here and never touch an LLM judge.

**Tier 2 is a single LLM judge** with a structured rubric. It fires when Tier 1 passes but the action requires semantic evaluation — is this analysis actually correct? Does this answer address the user's question? One model call, moderate cost.

**Tier 3 is a multi-persona panel** with multiple judge skills and consensus logic. It fires when Tier 2 returns low confidence or the action crosses a sensitivity threshold. On panel disagreement, the system escalates to human-in-the-loop review.

The governance overhead target is 10-20% of token cost in typical use. In practice, most of our Sterling workspace interactions clear at Tier 1 and never invoke an LLM judge at all.

### Where Rynko Flow fits

SwarmKit is vendor-neutral on validation providers. Any MCP server that exposes validation tools can serve as a judicial-tier evaluator.

[Rynko Flow](https://rynko.dev) is one option — it's a validation gateway I built that combines schema rules, expression-based business rules, and optional AI judges with human approval via magic links and an immutable audit trail. In SwarmKit, it integrates through a standard MCP tool skill. The distinction matters: AGT handles action-level enforcement (may this agent invoke this tool?), while Rynko Flow handles semantic-level validation (is this output correct?). They're complementary layers, and users can mix validation providers freely in the same topology.

### Honest about what's implemented

I should be clear about the gap between design and implementation. The four pillars run as logically separated modules in a single process today — distinct interfaces and storage boundaries, but not separate processes. Full process-level isolation is a v2.0 target. The automatic judicial tier escalation (Tier 2 low confidence → auto-route to Tier 3 panel) is designed but not yet wired end-to-end. The governance boundaries are real — every agent execution and MCP tool call goes through `evaluate_action` — but the full vision isn't complete yet.

## The CLI toolbox

SwarmKit ships a comprehensive CLI that covers the full lifecycle from workspace creation to production operation.

**Creating and authoring:** `swarmkit init .` scaffolds a complete workspace through conversation. `swarmkit author skill .` creates new skills conversationally, validates against JSON Schema, and corrects errors automatically. The same pattern works for topologies, archetypes, and MCP servers. All authoring commands use `prompt_toolkit` for arrow keys, history search, and persistent history.

**Running and chatting:** `swarmkit run . topology-name --input "..."` executes a one-shot run. `swarmkit chat . topology-name` starts a multi-turn interactive session where context persists across turns. You can resume previous conversations with `swarmkit conversations . --pick`. In chat mode, `/model` switches the LLM at runtime.

**Observability:** `swarmkit logs .` shows run history. `swarmkit status .` gives a summary. `swarmkit why <run-id> .` asks an LLM to explain what happened. `swarmkit ask "why was the last run slow?"` is a conversational observer.

**Production:** `swarmkit serve . --port 8000` runs a FastAPI HTTP server that accepts tasks via REST.

**Eject:** `swarmkit eject` is designed to export standalone LangGraph code from a topology — a clean ownership transfer if you ever want to leave the framework. It's not yet implemented, but the architectural constraint it imposes is real: every runtime feature must have an ejection story. If a feature can't be expressed in generated LangGraph code, we reconsider whether it belongs in the framework.

## Use cases

SwarmKit is general-purpose, but the architecture is particularly well suited to a few patterns.

**Enterprise knowledge assistants** — when your team has thousands of product docs, configuration dumps, API documentation, and project code scattered across different systems, a multi-agent topology with specialised searchers produces dramatically better answers than a single RAG pipeline.

**Code review and quality assurance** — a multi-leader topology with workers for code quality, security, test coverage, and deployment risk. Each review produces a structured verdict with confidence scores.

**Content creation pipelines** — a coordinator routes topics to a researcher, writer, SEO reviewer, and editor. Different models at different temperatures for each role.

**Document processing** — schema-driven extraction, validation against business rules, and output generation. Each step is a different agent with governance ensuring no agent approves its own output.

## The Sterling OMS workspace in depth

The most complete example in the repo is the Sterling OMS workspace, which we use daily on a real IBM Sterling implementation project.

The workspace has five topologies (sterling-assistant, solution-review, sterling-qa, code-review, coding-assistant), five archetypes, twenty-one skills, and nine MCP tool servers covering CDT configuration, ChromaDB vector search over 17K product docs, SQLite FTS5 exact keyword search, structured API javadocs for 1,006 APIs, a code knowledge graph, GitHub access, and filesystem tools for project code and notes.

### Cross-consultation in practice

When someone asks "how are sourcing rules managed in this project?", the sterling-assistant topology routes to both the architect and the developer. The architect searches project documentation, CDT configuration, and API references simultaneously, finding that sourcing rules flow from SAP to OMS via a JMS queue and then to the Inventory Cache microservice. The developer greps the Java source, finds `SourcingRuleFileUploadAgent.java`, reads specific line ranges, and traces the actual `createElement` and `setAttribute` calls.

The root synthesises both into a single answer with the integration architecture from one side and the actual code with line references from the other.

### Cost in practice

507 API requests, 1.9 million tokens, $0.33 total. Llama-3.3 for routing at $0.10/M, deepseek-chat for workers at $0.32/$0.89/M, all tool calls local and free.

## Skills and the public MCP ecosystem

There are over 7,000 community MCP servers. When someone needs a new capability, the first question isn't "how do I write this?" but "which existing MCP server does this?" A skill that wraps a public server is three lines of configuration in the workspace YAML plus a skill definition.

For capabilities without a public server — like parsing Sterling CDT XML dumps or providing structured API javadoc access — you write a custom MCP server. `swarmkit author mcp-server .` scaffolds these through conversation.

## What we've learned

**Tool names drive model behaviour more than prompts.** When we had `get-api-input-xml` in the developer's tool list, the model called it every time someone asked about XML — regardless of prompt instructions. Removing the tool fixed it immediately. Shape agent behaviour through tool availability, not prompt engineering.

**The synthesis step changed everything.** One additional LLM call where the agent sees its own tool results and writes a coherent answer turned the system from a developer tool into something the whole team could use.

**Conversation context across turns matters.** When workers didn't see prior findings, they'd re-grep for files already discovered. Adding conversation history eliminated redundant tool calls.

**Deterministic verification catches what prompts can't.** Our `verify_code_citations` tool checks every `file.java:line` reference against actual source at zero cost. It doesn't solve the interpretation problem, but it raises the floor.

## Getting started

```bash
pip install swarmkit-runtime
export OPENROUTER_API_KEY=sk-or-...
mkdir my-swarm && cd my-swarm
swarmkit init .
```

The authoring agent walks you through creating a workspace from scratch. A working swarm from an empty directory in under 15 minutes is the bar we're holding ourselves to.

The design doc (`design/SwarmKit-Design-v0.6.md`) covers the architecture in detail. If you want to understand why a decision was made, the section reference is in the code comments.

Open source at [github.com/delivstat/swarmkit](https://github.com/delivstat/swarmkit). MIT license.
