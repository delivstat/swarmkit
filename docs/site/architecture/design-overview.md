# Design overview

The authoritative architecture is [`design/SwarmKit-Design-v0.6.md`](https://github.com/delivstat/swarmkit/blob/main/design/SwarmKit-Design-v0.6.md).

## Three pillars

1. **Topology as data.** Swarms are YAML files the runtime interprets. Not Python classes, not generated code. Portable, version-controllable, diffable in PRs, editable in any text editor.

2. **Skills as the only extension primitive.** Every capability an agent can exercise is a skill. Four categories: capability (do something), decision (evaluate something), coordination (hand off), persistence (remember). One mental model, one contribution surface.

3. **Growth through human-approved authoring.** Swarms observe capability gaps via skill gap logs. The authoring system turns natural-language descriptions into tested, validated skills. Humans approve every step.

## Three-component system

| Component | Language | Role |
|-----------|----------|------|
| **swarmkit-runtime** | Python | Topology interpreter, LangGraph compiler, skill execution, governance, CLI + HTTP server |
| **swarmkit-schema** | JSON Schema + Python + TypeScript | Canonical schemas for 5 artifact types, validators, codegen |
| **swarmkit-ui** | Next.js | Topology composer, dashboard (v1.1 — CLI is the v1.0 entry point) |

## How LangGraph is used

The `compile_topology()` function transforms YAML topology into a LangGraph `StateGraph`:

1. Each agent becomes a LangGraph **node** with an async function that handles governance checks, LLM calls, and tool execution
2. The root connects to `START`. Children are reachable via **conditional edges** triggered by `delegate_to_<child>` synthetic tool calls
3. The graph state (`SwarmState`) carries input, current agent, accumulated results, conversation history, and final output through the graph
4. When an agent returns without delegating, execution flows back to the parent. When the root produces a final text response, the graph routes to `END`

### Multi-turn tool loop

After an agent makes tool calls, the runtime feeds results back to the model for synthesis. If the model needs more data, it makes additional tool calls — up to 8 rounds (configurable via `SWARMKIT_MAX_TOOL_TURNS`). The loop continues until the model produces a final text answer.

If the model responds with planning language ("let me examine...") without calling tools, the runtime detects this and nudges it to act.

### Per-agent model control

Each agent resolves to its own model provider at runtime. A topology can mix llama-3.3 (routing, $0.10/M) with deepseek-chat (reasoning, $0.32/M) with Ollama (local, $0). The `ModelProvider` abstraction supports 7 built-in providers.

## Governance: separation of powers

Four pillars with separate responsibility and runtime boundaries:

- **Legislative**: Topology YAML, IAM policies, scope definitions. Written by humans, immutable at runtime.
- **Executive**: All agents. Bounded authority — cannot grant themselves new scopes or modify topology.
- **Judicial**: Validation gates, LLM judges, schema checks. Independent — workers cannot influence their evaluators.
- **Media**: Append-only audit log, observability, review queues, skill gap logs.

### Judicial tiering

| Tier | Implementation | Cost | When |
|------|---------------|------|------|
| 1 | AGT policy engine (deterministic) | Sub-ms, $0 | Always — every action |
| 2 | Single LLM judge with rubric | Moderate | When semantic evaluation needed |
| 3 | Multi-persona panel + consensus | Expensive | Low confidence or sensitive actions |

Target: 10-20% governance overhead, not 300-400%.

### GovernanceProvider abstraction

Narrow interface (4 methods: evaluate_action, verify_identity, record_event, get_trust_score) that keeps SwarmKit portable. AGT is the v1.0 implementation. If AGT stagnates, a new implementation replaces it without changes to topology schema or runtime.

## Key design sections

- **§5-§6** — mental model: topology / agent / archetype / skill
- **§7** — architectural principles (tie-breakers for design decisions)
- **§8** — Separation of Powers governance model
- **§9** — three-component system
- **§10** — topology schema
- **§12** — skill authoring / swarm evolution
- **§14** — runtime architecture + CLI entry points
- **§16** — AGT integration details
- **§18** — MCP integration
- **§21** — open questions
