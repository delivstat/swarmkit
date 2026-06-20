---
title: What to borrow from Google ADK (and what not to)
description: ADK is a code-first peer framework, not a better substrate for SwarmKit's data-defined core. LangGraph stays the engine. These are the ideas worth borrowing — eval harness, workflow primitives, agent-as-skill/A2A, uniform interception, trace inspection — most of which sharpen SwarmKit's own pillars.
tags: [strategy, adk, langgraph, eval, a2a, interop]
status: proposal
---

# What to borrow from Google ADK

## Position

We committed to **LangGraph as the execution core** because SwarmKit is
**topology-as-data → compiled to a graph** (§5–§6, §10, §14): an arbitrary
data-defined topology maps ~1:1 onto a LangGraph `StateGraph`, and LangGraph's
low-level explicit-graph model is the *right compile target* precisely because it's
unopinionated. **Google ADK is a code-first *peer* framework** (you write agents in
it), not a better *substrate* to build SwarmKit on. Even greenfield, the choice
holds — and ADK's biggest limitation (Gemini/GCP gravity) collides with invariant #4
(no vendor lock-in).

So this isn't "switch". It's "borrow the good ideas", and the tell that an idea is a
*good* borrow is that it sharpens an existing SwarmKit pillar rather than importing a
foreign concept.

## Borrows (priority order)

1. **Eval harness — the #1 borrow (genuine gap).** SwarmKit has decision-skill judges
   (`governance/_decision_evaluator.py`) but no topology-level eval/benchmark
   framework. Borrow ADK's eval-sets model: `input → expected trajectory + response`
   as **data artifacts**, a `swarmkit eval` command, scored by **reusing the existing
   decision skills**. It is the "test" gate in growth-through-authoring (§12) and the
   "Measure" signal in [[fleet-control-plane]]. → roadmap **M15**.
2. **Deterministic workflow primitives** — Sequential / Parallel / Loop as named
   **topology archetypes**. Reinforces topology-as-data + "LLM does language, code does
   the doing". → roadmap **M18**.
3. **Agent-as-skill / A2A interop** — model a remote A2A agent / sub-swarm /
   another instance's swarm as a **coordination skill** (keeps "skills are the only
   extension primitive"); builds on the A2A adapter (`_delegation.py`) + §18. → **M18**.
4. **Uniform interception surface** — SwarmKit *already* intercepts uniformly via
   `GovernanceProvider` + OTel at every model/tool/skill/agent call (more principled
   than ADK's ad-hoc callbacks). Only borrow: route **eval + self-improvement signals**
   through the same contract, not a side channel.
5. **Trace inspection** — per-instance in the existing serve/UI (edge/dev, Minder);
   fleet-wide in the [[fleet-control-plane]] cockpit.

## Do NOT borrow

- **Managed GCP/Vertex deploy + Cloud Trace** — collides with invariant #4. Only ever
  as an `eject`-to-ADK *export*, never the core.
- **Gemini-first defaults / LiteLLM** — the ModelProvider abstraction
  ([[model-provider-abstraction]]) already covers multi-provider, more cleanly.

## Interop, not exclusivity

ADK and LangGraph interoperate: ADK can wrap a LangGraph agent as an `AgentTool`;
LangGraph can call ADK agents as subgraphs; both speak MCP + A2A. The posture is
**LangGraph core + A2A/ADK interop** — a SwarmKit swarm can *orchestrate* ADK-built
agents without adopting ADK as its engine. Adopt **A2A** as the agent-level peer of
MCP's tool-level integration (§18).
