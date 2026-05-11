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
  <img src="https://img.shields.io/badge/tests-500+-green.svg" alt="500+ tests" />
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

**Coming in M7:** intent drift detection — embedding-based drift scoring with nudge strategies.

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

The [`examples/sterling-oms/`](./examples/sterling-oms/) workspace demonstrates reasoning over 1,000+ API javadocs with multiple MCP servers (ChromaDB vector search, FTS5 keyword search, CDT config server) — a production-grade setup for enterprise domain knowledge.

## Install from source

Prerequisites: Python 3.11+, Node 20+, [`uv`](https://docs.astral.sh/uv/), `pnpm`, `just`.

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and build
git clone git@github.com:delivstat/swarmkit.git && cd swarmkit
just install          # uv sync + pnpm install
just test             # 500+ tests across Python + TypeScript
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

See [`design/IMPLEMENTATION-PLAN.md`](./design/IMPLEMENTATION-PLAN.md) for the full 4-phase roadmap. M6 (observability) complete. Current focus: M6.5 (workspace env config), then M7 (intent drift detection).

## Contributing

Every change goes through a PR. See [`CLAUDE.md`](./CLAUDE.md) for the feature delivery workflow, invariants, and style guide.

## License

MIT — see [LICENSE](./LICENSE).
