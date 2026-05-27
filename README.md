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
pip install swarmkit-runtime  # or: uv tool install swarmkit-runtime
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
# Option 1: uv (recommended — fast, no venv needed)
curl -LsSf https://astral.sh/uv/install.sh | sh   # install uv if you don't have it
uv tool install swarmkit-runtime

# Option 2: pip
pip install swarmkit-runtime
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

### HTTP server + canary deployments (M10 — shipped)

`swarmkit serve` runs your workspace as a persistent HTTP service with async job execution, SSE streaming, webhook triggers, MCP endpoint, and pluggable auth:

```bash
swarmkit serve my-swarm/ --port 8000

# Submit jobs via API
curl -X POST http://localhost:8000/run/my-topology \
  -H "Content-Type: application/json" \
  -d '{"input": "Process this request"}'
```

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
