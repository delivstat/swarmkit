# Guided tutorials

Learn SwarmKit from zero to production through 22 progressive levels. Levels 1–16 build on one workspace, adding complexity incrementally; levels 17–22 each take one shipped capability — harness executors, funnels, command packs and attachments, agents calling agents, operations, the fleet — and are runnable on the mock provider where the level says so.

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
| 15 | [Production Example](15-production-example.md) | Full workspace combining levels 1–14 |
| 16 | [Sequencing & Contracts](16-pipelines.md) | Correlated runs, defer and resume, gate state, contracts |
| 17 | [Harness executors](17-harness-executors.md) | Claude Code / opencode as a node, adapters, the governed gateway, relay + trust, sandbox |
| 18 | [Funnels & approval](18-funnels-approval.md) | validate → judge → approve, role registry, quorum, `--require-verified`, `cited-change`, `stop` |
| 19 | [Command packs & attachments](19-command-packs-attachments.md) | A binary as a skill, `pack:` grants, a file beside the input |
| 20 | [Agents calling agents](20-agents-calling-agents.md) | `agent` skills, `pack:workspace`, A2A server + client, the portal's remote agents |
| 21 | [Providers, storage & operations](21-providers-storage-operations.md) | Declarative providers, storage status/migrate, `system`, `eval`, `knowledge-pack` |
| 22 | [Running a fleet](22-fleet.md) | Control plane, enrolment (Mode A / B), federated runs and gates, registry, telemetry |

## How to use

Each level has:
- **What you'll learn** — features covered
- **Build it** — step-by-step instructions with YAML
- **Run it** — commands to test
- **What happened** — explanation of the output

**`just demo-capstone`** runs every level's HTTP-reachable feature in one workspace (`examples/capstone`), on the mock provider, in under a minute. Runnable demos for levels 17–22 are `just demo-*` targets named in each level; `just` with no arguments lists them all. `examples/` holds the workspaces the demos run.

## Beyond the tutorials

After Level 22, see the **[SDLC walkthrough](../sdlc-example/)** — a video tour of a production delivery workspace that combines the level features with the governance primitives: [Funnels](../reference/funnel.md), multi-party [approval](../reference/approval-policy.md), and [Contracts](../reference/contract.md). (Recorded before sequencing moved out of SwarmKit in 1.189.0; the artifact tour is current, the stage-graph sections are historical.)

Start with [Level 1: Hello World](01-hello-world.md).
