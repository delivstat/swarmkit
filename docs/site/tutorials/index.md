# Guided tutorials

Learn SwarmKit from zero to production through 15 progressive levels. Each level builds on the same workspace, adding complexity incrementally.

## Prerequisites

```bash
# Install SwarmKit
uv tool install swarmkit-runtime

# Verify
swarmkit --help
```

For real LLM calls (Level 3+), set an API key:
```bash
export OPENROUTER_API_KEY=your-key-here
```

## Levels

| Level | Topic | Features covered |
|-------|-------|-----------------|
| 1 | [Hello World](01-hello-world.md) | Install, workspace, one agent, `validate`, `run` |
| 2 | [Archetypes](02-archetypes.md) | Reusable agent configs, model settings, prompts |
| 3 | [Skills](03-skills.md) | Capability, decision, coordination skills |
| 4 | [Multi-Agent](04-multi-agent.md) | Hierarchy, delegation, parallel execution, DAG |
| 5 | [MCP Tools](05-mcp-tools.md) | Custom MCP servers, permission tiers, sandboxing |
| 6 | [Structured Delegation](06-structured-delegation.md) | Task plans, scopes, two-phase planning, dual model |
| 7 | [Governance & Safety](07-governance.md) | Decision gates, IAM scopes, circuit breakers, HITL |
| 8 | [Observability](08-observability.md) | Tracing, drift detection, debugging CLI, OTel |
| 9 | [Conversations & Memory](09-conversations-memory.md) | `chat`, memory-reader/writer, GBrain integration |
| 10 | [Knowledge & RAG](10-knowledge-rag.md) | ChromaDB, GBrain, knowledge MCP, document reader |
| 11 | [Serve & HTTP API](11-serve-api.md) | `swarmkit serve`, REST endpoints, SSE, auth |
| 12 | [Triggers & Canary](12-triggers-canary.md) | Cron, webhooks, canary deployments |
| 13 | [Authoring & Review](13-authoring-review.md) | `init`, `author`, `edit`, review queues, skill gaps |
| 14 | [Packaging & Distribution](14-packaging.md) | `mcp-serve`, `publish`, `install`, expertise packages |
| 15 | [Production Example](15-production-example.md) | Full workspace combining all features |

## How to use

Each level has:
- **What you'll learn** — features covered
- **Build it** — step-by-step instructions with YAML
- **Run it** — commands to test
- **What happened** — explanation of the output

Working examples for each level are at `examples/tutorials/`.

## Beyond the tutorials

After Level 16, see the **[SDLC pipeline walkthrough](../sdlc-example/)** — a video tour of a production delivery pipeline that combines the level features with the orchestration primitives: [Funnels](../reference/funnel.md), [StageGraphs](../reference/stage-graph.md), and [Contracts](../reference/contract.md).

Start with [Level 1: Hello World](01-hello-world.md).
