<p align="center">
  <h1 align="center">SwarmKit</h1>
  <p align="center">
    <strong>Multi-agent AI swarms as YAML, not code.</strong>
    <br />
    Define agents, skills, and governance in a topology file. SwarmKit compiles it to LangGraph and runs it.
  </p>
</p>

<p align="center">
  <a href="https://github.com/delivstat/swarmkit/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" /></a>
  <a href="https://pypi.org/project/swarmkit-runtime/"><img src="https://img.shields.io/pypi/v/swarmkit-runtime.svg" alt="PyPI" /></a>
  <a href="https://github.com/delivstat/swarmkit/actions"><img src="https://img.shields.io/github/actions/workflow/status/delivstat/swarmkit/ci.yml?branch=main" alt="CI" /></a>
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/tests-566-green.svg" alt="566 tests" />
</p>

<!-- TODO: Record the demo GIF with: vhs scripts/demo.tape -->
<!-- Then uncomment: -->
<!-- <p align="center"><img src="docs/images/demo.gif" alt="SwarmKit demo" width="800" /></p> -->

---

## The problem

Building multi-agent systems with LangGraph means writing hundreds of lines of Python for every topology: node functions, edge routing, state management, tool wiring, governance, error handling. Change the agent structure and you're refactoring code, not configuration.

## The fix

```yaml
# This is a complete 10-agent code review swarm. No Python.
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: code-review
agents:
  root:
    id: supervisor
    archetype: supervisor-leader
    children:
      - id: engineering-leader
        archetype: engineering-leader
        children:
          - id: code-reviewer
            archetype: code-analyst
            skills: [code-quality-review, security-scan]
          - id: github-reader
            archetype: github-reader
            skills: [github-pr-read]
      - id: qa-leader
        archetype: qa-leader
        children:
          - id: test-analyst
            archetype: test-analyst
            skills: [test-coverage-review, run-tests]
```

```bash
uv tool install swarmkit-runtime
swarmkit run my-swarm/ code-review --input "Review PR #49"
```

SwarmKit compiles this YAML to a LangGraph `StateGraph`, wires MCP tool servers, enforces governance policies, and runs the swarm. You keep the full power of LangGraph (checkpointing, streaming, state management) without writing the boilerplate.

## Why SwarmKit over alternatives

| | SwarmKit | LangGraph (raw) | CrewAI | Claude Agent SDK |
|---|---|---|---|---|
| Agent definition | YAML topology | Python code | Python classes | Code + config |
| Multi-agent orchestration | Declarative hierarchy + DAG | Manual graph construction | Role-based | Single agent loop |
| Tool integration | 7,000+ MCP servers via YAML config | Build or wire yourself | Built-in + MCP | Built-in harness + MCP |
| Governance / permissions | IAM scopes + policy engine (AGT) | DIY | None | None |
| Audit trail | Hash-chained, append-only | DIY | None | None |
| Human-in-the-loop | Native approval gates in YAML | Manual interrupt points | None | None |
| Escape hatch | `swarmkit eject` to pure LangGraph (planned) | N/A | None | None |
| Model support | 7 providers (Anthropic, OpenAI, Google, Ollama, ...) | Any | Multiple | Claude only |

## Quick start

### Install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # install uv if you don't have it
uv tool install swarmkit-runtime
```

### Create and run a swarm

```bash
# Create a workspace through conversation (you never write YAML)
swarmkit init my-swarm/

# Run it
swarmkit run my-swarm/ my-topology --input "Do the thing"

# Or use the reference code review swarm out of the box
swarmkit run reference/ code-review --input "Review PR #49 on delivstat/swarmkit"
```

### 30-second workflow

```bash
swarmkit init my-swarm/                                # conversational workspace creation
swarmkit validate my-swarm/ --tree                     # validate + show agent tree
swarmkit run my-swarm/ my-topology --input "Greet us"  # run end-to-end
swarmkit chat my-swarm/ my-topology                    # multi-turn conversation
swarmkit author skill my-swarm/                        # add skills conversationally
swarmkit edit my-swarm/ --input "Add a security scan"  # modify via conversation
```

## How it works

<p align="center">
  <img src="docs/images/architecture.svg" alt="SwarmKit architecture" width="800" />
</p>

## Key features

### Topology as data

Swarms are YAML files, not Python. Declare agents, hierarchy, skills, model preferences, and IAM scopes. The runtime interprets them — no code generation.

### Skills as the only extension

Need custom logic? Write a skill (LLM prompt or MCP server), not a Python plugin. SwarmKit's CLI can even write skills for you:

```bash
swarmkit author skill my-swarm/                # single-agent authoring
swarmkit author skill my-swarm/ --thorough     # multi-agent authoring swarm
swarmkit author mcp-server my-swarm/           # generate an MCP server
```

### 7,000+ tools via MCP

Wire any MCP server in YAML. GitHub, databases, Slack, browsers, filesystems — no building tools from scratch:

```yaml
mcp_servers:
  - id: github
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
```

Sandboxed execution available: `sandboxed: true` runs MCP servers in Docker with `--network=none` and read-only mounts.

### Governance built in

Every tool call goes through `evaluate_action` before execution. IAM scopes per agent. Hash-chained audit trail via Microsoft AGT. Mock provider for dev, AGT for production:

```yaml
governance:
  provider: agt
  config:
    policies_dir: ./policies
```

### 7 model providers

Auto-detected from environment variables. Mix providers within a single topology:

| Provider | Env var | Example |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| Google | `GOOGLE_API_KEY` | `gemini-2.5-flash` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` |
| OpenRouter | `OPENROUTER_API_KEY` | `meta-llama/llama-3.3-70b-instruct` |
| Groq | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |
| Together | `TOGETHER_API_KEY` | `meta-llama/llama-3.3-70b` |
| Ollama | (always available) | `llama3.3` |

### Observability (M6 — shipped)

Every run records structured audit events to SQLite (`.swarmkit/audit.sqlite`). OpenTelemetry traces, metrics, governance circuit breakers, notification plugins, and a local prompt ring buffer are all built in.

```bash
swarmkit status my-swarm/                      # recent runs from audit store
swarmkit logs my-swarm/ --last 3               # detailed events (--run-id, --agent filters)
swarmkit why <run-id> my-swarm/                # LLM explains what happened
swarmkit ask "Which agents are slowest?" -w .  # conversational observer (--run scoping)
swarmkit debug my-swarm/ --span-id <id>        # retrieve prompts from local ring buffer
swarmkit review list my-swarm/                 # pending human reviews
swarmkit gaps my-swarm/                        # recorded skill gaps
```

Per-skill audit redaction — sensitive fields are `[REDACTED]` before storage:

```yaml
audit:
  log_inputs: summary
  log_outputs: full
  redact: ["$.password", "$.api_key"]
```

OTel traces to any backend: `SWARMKIT_OTEL_EXPORTER=console swarmkit run ...`

Intent drift detection (M7 — shipped): detects when agents wander from the original goal. Add `intent_monitoring: { enabled: true, threshold: 0.75, on_drift: nudge }` to your topology.

### Workspace memory — agents that remember (shipped)

Agents remember across conversations. After each turn, structured insights (topic, context, key points) are extracted and saved. Before the next conversation, relevant prior context is injected into the agent's prompt. The agent references past sessions naturally — "As we discussed previously..."

```yaml
governance:
  decision_skills:
    - id: memory-reader
      trigger: pre_input
      scope: "*"
    - id: memory-writer
      trigger: post_output
      scope: "*"
```

Two backends: local JSON store (zero setup) or GBrain MCP server (hybrid vector + keyword search, graph relationships, Supabase/Postgres for production). See [`docs/reference/workspace-memory.md`](./docs/reference/workspace-memory.md) and [`docs/examples/memory-demo.py`](./docs/examples/memory-demo.py).

### HTTP server + canary deployments (M10 — shipped)

`swarmkit serve` runs your workspace as a persistent HTTP service with async job execution, SSE streaming, webhook triggers, MCP endpoint, and pluggable auth:

```bash
swarmkit serve my-swarm/ --port 8000

# Submit jobs via API
curl -X POST http://localhost:8000/run/my-topology \
  -H "Content-Type: application/json" \
  -d '{"input": "Process this request"}'
```

**The web portal ships with the runtime.** Install the `[ui]` extra and `swarmkit serve` hosts the portal at its own origin — no Node, no separate process, no CORS, no API-URL env var (the portal talks to the workspace serve was started with):

```bash
pip install "swarmkit-runtime[ui]"
swarmkit serve my-swarm/                # → portal AND API on http://localhost:8000
```

Without the extra, serve runs headless (API only), unchanged.

**Canary deployments** let you gradually roll out topology changes — split traffic between versions, monitor error rates and drift, auto-promote when criteria are met:

```yaml
server:
  canary:
    routes:
      - topology: my-swarm
        versions:
          - version: "1.0.0"
            weight: 90
          - version: "1.1.0"
            weight: 10
            promote_when:
              min_runs: 50
              error_rate_below: 0.05
              drift_below: 0.30
```

Auth providers (API key, JWT with JWKS auto-discovery) and webhook HMAC signature validation are plug-and-play — disabled by default, enabled via workspace config. See [`docs/reference/serve-cli-tests.md`](./docs/reference/serve-cli-tests.md) and [`docs/reference/canary-deployments.md`](./docs/reference/canary-deployments.md).

### Run trace (M8 — shipped)

Every run saves a structured trace showing the agent call graph, tool usage, and token counts per agent and model.

```bash
swarmkit trace -w .            # list recent runs with token counts
swarmkit trace <run-id> -w .   # show full call graph for a run
```

### Structured delegation (M9 — shipped)

Planner-driven task execution. Coordinators call `create-task-plan` to produce a dependency-ordered plan; the compiler executes independent tasks in parallel and dependent tasks sequentially. Plans are crash-resilient (`tasks.json` on disk) — the CLI detects previous plans on fresh runs. Results are summary-first (3-5 bullet key_findings, full results on disk). Auto-fix adds missing dependencies and synthesis tasks.

### Sub-agent architecture (M8 — shipped)

Agents can delegate to focused sub-agents instead of handling everything with one overloaded tool set. A coordinator with 40+ tools becomes a coordinator with 4 delegate tools orchestrating focused researchers — each with 10-12 tools and their own 25-turn tool budget.

### Multimodal support (M8 — shipped)

Image content blocks across all 7 model providers. MCP tools can return `ImageContent` for vision models. `view_image` tool lets agents see diagrams, screenshots, and architecture drawings. MarkItDown integration for document reading with inline images.

### MCP permission tiers (M8 — shipped)

Per-server and per-tool governance: `permission: open|cautious|strict|readonly` in workspace.yaml. Reads auto-approved, writes need governance approval, readonly denies mutations.

### Harness executors (M19 — shipped, sandbox rolling out)

Run a coding harness (Claude Code, opencode, and any subprocess that emits line-delimited JSON) as an agent node, not just a model. Harnesses are **data**: a declarative `adapter.yaml` — no per-harness Python — interpreted by one engine, with a bundled library for the big harnesses. Isolated in an ephemeral git worktree by default; mid-run out-of-grant permissions **relay** to a human inbox and resume; repeated approvals **accrue** into a proposed allowlist changeset (`swarmkit trust`). An **opt-in container sandbox** adds real isolation — resource limits, enforced egress (`deny`/`allowlist`), and a `build` step that runs the harness with **no local install** (bring only your API key). Off by default; `SWARMKIT_DISABLE_CONTAINER_SANDBOX` always wins. See [the adapter guide](docs/guides/authoring-harness-adapters.md).

## Complete feature list

### Topology & Agent Orchestration
1. **Topology as data** — define swarms in YAML, not Python code
2. **Agent hierarchy** — root, leader, worker roles with delegation
3. **Parallel delegation** — multiple child agents execute concurrently
4. **DAG dependencies** — `depends_on` for execution ordering across agents
5. **Structured delegation** — task plans with `create-task-plan`, `create-scope`, two-phase planning
6. **Synthesis** — automatic output synthesis with configurable model and prompt
7. **Per-agent model override** — different models per agent in the same topology
8. **Dual model support** — cheap model for tool calls, quality model for synthesis
9. **Output schema** — JSON Schema enforcement on agent outputs
10. **Checkpointing** — resume interrupted runs from saved state (SQLite/Postgres)

### Skills & Tools
11. **Four skill categories** — capability, decision, coordination, persistence
12. **Three implementation types** — `mcp_tool`, `llm_prompt`, `composed`
13. **7,000+ MCP tools** — wire any MCP server via YAML config
14. **Custom MCP servers** — build your own in Python/Node with stdio transport
15. **MCP permission tiers** — open, cautious, strict, readonly per server/tool
16. **MCP sandboxing** — Docker isolation with `--network=none` and read-only mounts
17. **Lazy MCP startup** — servers start on first tool call, not at boot
18. **Multimodal support** — image content blocks, document reader, MarkItDown
19. **Composed skills** — parallel-consensus, sequential, or custom composition
20. **Skill gap detection** — auto-detect missing skills, surface after threshold

### Governance & Safety
21. **IAM scopes** — per-agent permission model (`repo:read`, `skills:activate`)
22. **Decision skill gates** — pre_input / post_output validation on every turn
23. **Policy evaluation tiers** — deterministic, single LLM judge, panel
24. **Circuit breakers** — max steps, max cost, max tokens per run
25. **Trust levels** — tiered access control for agents
26. **Audit trail** — append-only structured events (SQLite/Postgres)
27. **Audit redaction** — JSON pointer paths to redact sensitive fields
28. **Human-in-the-loop** — review queues with approve/reject workflow
29. **Gate validators** — drop-in JSON Schema files in `gates/` directory
30. **Output validation** — structured output enforcement with auto-correction

### Observability & Debugging
31. **OpenTelemetry** — traces, metrics, spans to any OTel backend
32. **Intent drift detection** — cosine similarity tracking, nudge/warn/log actions
33. **Run tracing** — agent call graph, tool calls, token counts per agent/model
34. **Prompt ring buffer** — local SQLite cache of all prompts/responses
35. **CLI debugging** — `logs`, `trace`, `why`, `ask`, `debug`, `status` commands
36. **Notification plugins** — configurable alerts on run events
37. **Tool call recording** — every MCP call tracked with arguments, result size, duration

### Memory & Knowledge
38. **Workspace memory** — agents remember across conversations (MemoryStore or GBrain)
39. **Memory gates** — pre_input context injection, post_output insight extraction
40. **GBrain integration** — hybrid vector + keyword search, graph relationships
41. **Knowledge MCP server** — workspace docs, schema queries, file I/O
42. **Document reader** — PDF, DOCX, Excel, CSV, SVG, draw.io parsing

### Conversations & Authoring
43. **Multi-turn chat** — `swarmkit chat` with persistent history
44. **Conversation persistence** — saved to disk, resume by ID
45. **Conversational authoring** — `swarmkit init/author/edit` create artifacts via conversation
46. **Thorough mode** — multi-agent authoring swarm for complex artifacts

### HTTP Server & Deployment
47. **Persistent serve mode** — `swarmkit serve` with async job execution
48. **SSE streaming** — real-time progress events during execution
49. **REST API** — CRUD endpoints for topologies, skills, archetypes
50. **Auth providers** — None, API key, JWT (JWKS auto-discovery)
51. **Canary deployments** — weighted traffic splitting, auto-promotion by metrics
52. **Cron triggers** — scheduled topology execution
53. **Webhook triggers** — HMAC-SHA256 signature validation
54. **MCP endpoint** — expose topologies as MCP tools for AI assistants
55. **Concurrent job limiting** — semaphore-based with configurable max

### Packaging & Distribution
56. **Expertise packages** — bundle workspaces for distribution
57. **`swarmkit mcp-serve`** — expose workspaces to Claude Desktop, Cursor, Claude Code
58. **`swarmkit install/publish`** — install from directory, tarball, or URL

### Model Providers
59. **7 providers** — Anthropic, OpenAI, Google, OpenRouter, Groq, Together, Ollama
60. **Auto-detection** — providers activated from environment variables
61. **Per-agent provider** — mix providers within a single topology
62. **Prompt caching** — automatic prefix caching (99% savings on DeepSeek)

### Developer Experience
63. **`swarmkit validate --tree`** — visual agent tree with skills, archetypes, MCP servers
64. **`swarmkit run --dry-run`** — show resolved agents without executing
65. **`swarmkit run --verbose`** — per-agent execution detail
66. **Web UI** — dashboard, chat, topology composer (+ node/edge canvas), skill/archetype editors; ships with the runtime (`pip install "swarmkit-runtime[ui]"` → `swarmkit serve` hosts the portal at its own origin)
67. **JSON & TypeScript schemas** — validators in both languages
68. **Reference topologies** — code-review (10 agents), skill-authoring (6 agents)
69. **16 archetypes + 25 skills** — production-ready out of the box

### Harness executors
70. **Harness as a node** — run Claude Code / opencode / any subprocess emitting JSONL as an agent
71. **Declarative adapters** — `adapter.yaml`, no per-harness Python; bundled library for the big harnesses
72. **Worktree isolation** — ephemeral git worktree per run by default; produces a diff, never integrates
73. **Relay approvals** — mid-run out-of-grant permissions pause to a human inbox and resume (`swarmkit review`)
74. **Trust accrual** — repeated approvals propose an allowlist changeset (`swarmkit trust list|apply|clear`)
75. **Opt-in container sandbox** — resource limits + enforced egress (`deny`/`allowlist`); off by default, disable switch always wins
76. **No-local-install `build`** — provision the harness into a cached image; bring only your API key

## Reference topologies

Ships with production-quality topologies you can use immediately:

**Code Review Swarm** — 3 leaders (Engineering, QA, Ops), 10 agents. Fetches PRs via GitHub MCP, reviews code quality + security + test coverage, HITL approval for deployment:

```bash
swarmkit run reference/ code-review --input "Review PR #49 on delivstat/swarmkit"
```

**Skill Authoring Swarm** — 6 specialist agents create SwarmKit artifacts through conversation, grounded by the Knowledge MCP Server:

```bash
swarmkit author skill my-workspace/ --thorough
```

**16 archetypes** and **20 skills** included under [`reference/`](./reference/).

## Real-world example

The [`examples/sterling-oms/`](./examples/sterling-oms/) workspace demonstrates enterprise-scale agent orchestration: 6 topologies, 11 archetypes, 70+ skills. A root coordinator delegates to an architect, which delegates to 6 focused workers (jira, config, docs, developer, log-analyst, document-writer). Includes an Atlassian wrapper MCP (structured JQL/CQL), a log analyser MCP (SQLite-indexed, 500MB+ logs, 9 tools), and a document writer with pandoc MCP for DOCX/PDF generation. Per-agent model selection: Kimi K2.5 for reasoning, DeepSeek V4 Flash for workers, DeepSeek Chat V3 for writing.

## Install from source

Prerequisites: Python 3.11+, Node 20+, [`uv`](https://docs.astral.sh/uv/), `pnpm`, `just`.

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and build
git clone git@github.com:delivstat/swarmkit.git && cd swarmkit
just install          # uv sync + pnpm install
just test             # 566 tests across Python + TypeScript
just lint             # ruff + biome
just typecheck        # mypy + tsc
```

## Monorepo layout

```
swarmkit/
├── design/              # Authoritative architecture (v0.6) + 30+ design notes
├── packages/
│   ├── runtime/         # Python: CLI, LangGraph compiler, governance, MCP
│   ├── schema/          # JSON Schemas + Python & TypeScript validators
│   └── ui/              # Next.js (v1.1 — extends CLI, doesn't replace it)
├── reference/           # 2 topologies, 16 archetypes, 20 skills
├── examples/            # hello-swarm, sterling-oms, rynko-content
├── docs/                # User-facing docs + discipline notes
└── llms.txt             # LLM-queryable index (llmstxt.org)
```

## For LLMs

SwarmKit docs are designed for LLM consumption. The repo ships [`llms.txt`](./llms.txt) at the root:

```bash
swarmkit knowledge-pack -o pack.md    # bundle everything for any LLM
swarmkit knowledge-server             # live MCP server for Claude Code / Cursor
```

## Roadmap

See [`design/IMPLEMENTATION-PLAN.md`](./design/IMPLEMENTATION-PLAN.md) for the full 4-phase roadmap. M0-M9 complete, M10 serve + canary shipped. Next: eject + launch prep.

## Contributing

Every change goes through a PR. See [`CLAUDE.md`](./CLAUDE.md) for the feature delivery workflow, invariants, and style guide.

## License

MIT — see [LICENSE](./LICENSE).
