# SwarmKit

> An open-source framework for composing, running, and growing multi-agent AI swarms.

SwarmKit treats swarm topology — who exists, who reports to whom, what skills they can exercise — as declarative data rather than imperative code. This separation lets non-developers compose agent teams conversationally while developers retain full programmatic control. Swarms are not static: every swarm can observe its own capability gaps and grow new skills through a conversational, human-approved authoring flow.

**Status:** v1.0 shipped. The framework runs multi-agent topologies end-to-end via CLI or HTTP server, with real LLM providers, MCP tool servers, governance enforcement, knowledge-grounded review, and conversational workspace editing. See [`design/IMPLEMENTATION-PLAN.md`](./design/IMPLEMENTATION-PLAN.md) for the full roadmap; [`design/SwarmKit-Design-v0.6.md`](./design/SwarmKit-Design-v0.6.md) is the authoritative architecture.

## What works today

```bash
# Create a workspace through conversation (never write YAML)
swarmkit init my-swarm/

# Author skills, topologies, archetypes conversationally
swarmkit author skill my-swarm/
swarmkit author skill my-swarm/ --thorough   # multi-agent authoring swarm

# Edit an existing workspace conversationally
swarmkit edit my-swarm/ --input "Add a dependency vulnerability scan skill"

# Validate a workspace — human-readable errors with file pointers
swarmkit validate my-swarm/ --tree

# Run a topology end-to-end
SWARMKIT_PROVIDER=openrouter SWARMKIT_MODEL=meta-llama/llama-3.3-70b-instruct \
  swarmkit run my-swarm/ my-topology --input "Do the thing"

# Dry run — see what would execute without hitting any LLM or MCP
swarmkit run reference/ code-review --dry-run

# Review the Code Review Swarm against a real GitHub PR
swarmkit run reference/ code-review --input "Review PR #49 on delivstat/swarmkit"

# Multi-turn conversation with a topology (context persists across turns)
swarmkit chat my-swarm/ my-topology
> Design ship-from-store for 200 locations
[swarm responds]
> Change the sourcing to cost-based
[swarm responds with context from the previous turn]

# List and resume previous conversations
swarmkit conversations my-swarm/ --pick

# Run with observability (per-agent timing, skills, denials)
swarmkit run my-swarm/ my-topology --input "Do the thing" --verbose

# View recent run history
swarmkit status my-swarm/

# Read detailed events from past runs
swarmkit logs my-swarm/ --last 3

# Ask an LLM to explain what happened in a run
swarmkit why hello-20260426T134042 my-swarm/

# Ask questions about the workspace or recent runs
swarmkit ask "Which agents are taking the longest?" -w my-swarm/

# Start the HTTP server (persistent mode)
swarmkit serve my-swarm/ --port 8000
curl -X POST http://localhost:8000/run/my-topology \
  -d '{"input": "Do the thing"}'

# Bundle the full SwarmKit corpus for any LLM
swarmkit knowledge-pack -o pack.md

# Launch the Knowledge MCP Server (live docs search for any MCP client)
swarmkit knowledge-server
```

## Milestone progress

See [`design/IMPLEMENTATION-PLAN.md`](./design/IMPLEMENTATION-PLAN.md) for full details.

### Phase 1 — Foundation (complete)

| # | Milestone | Status |
|---|---|---|
| M0 | Schemas (5 artifact types, dual-language validators, codegen) | ✅ |
| M1 | Topology loading and resolution | ✅ |
| M2 | GovernanceProvider + AGT integration | ✅ |
| M2.5 | ModelProvider abstraction (7 built-in providers) | ✅ |
| M3 | LangGraph compiler (capability + coordination + DAG) | ✅ |
| M3.5 | Conversational authoring (`swarmkit init/author/edit`) | ✅ |
| M4 | Decision skills, structured output, review queue, HITL | ✅ |
| M5 | MCP integration (stdio + HTTP, sandboxed servers, governance gating) | ✅ |

### Phase 2 — Runtime completion (current)

| # | Milestone | Status |
|---|---|---|
| M6 | Observability: OpenTelemetry traces, local ring buffer, CLI primitives, governance circuit breakers | Next |
| M7 | Intent drift detection: embedding-based drift scoring, nudge strategies | Planned |

### Phase 3-4 — Ecosystem + production readiness

| # | Milestone | Status |
|---|---|---|
| M8 | Knowledge + skills ecosystem: skill registry CLI, user knowledge server | Planned |
| M9 | Reference topologies: code review + skill authoring swarms runnable e2e | Planned |
| M10 | Eject + execution modes: `swarmkit eject`, HTTP server, canary deployments | Planned |
| M11 | Launch prep: docs site, PyPI/npm publish, expertise packages | Planned |

## Key features

### Topology as data

Swarms are YAML files the runtime interprets — not Python code. A topology declares agents, their hierarchy, model preferences, skills, and IAM scopes:

```yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: code-review
agents:
  root:
    id: root
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
```

### Skills as the only extension primitive

Capability, decision, coordination, persistence — one mental model. Skills can be backed by LLM prompts or MCP tool servers:

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

### MCP integration

Workspace-level MCP server registry. Stdio + HTTP transports. Sandboxed Docker isolation for generated servers. Tool schemas forwarded to LLM tool definitions automatically:

```yaml
mcp_servers:
  - id: github
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
  - id: swarmkit-knowledge
    transport: stdio
    command: ["uv", "run", "python", "-m", "swarmkit_runtime.knowledge"]
```

### Governance built in

AGT-backed policy enforcement, identity verification, hash-chained audit. Every MCP tool call goes through `evaluate_action` before execution. Mock provider for development; AGT for production:

```yaml
governance:
  provider: agt
  config:
    policies_dir: ./policies
```

### Knowledge-grounded agents

The Knowledge MCP Server exposes SwarmKit's own docs, schemas, and reference skills as live-searchable MCP tools. Code reviewers search the design doc before producing verdicts. The authoring swarm checks existing skills before generating new ones.

### Conversational authoring

`swarmkit init` creates a workspace through conversation. `swarmkit author` creates individual artifacts. `swarmkit edit` modifies existing swarms conversationally — describe what's wrong, the authoring swarm reads the workspace, drafts changes, validates, and writes.

### Observability

Every run records structured audit events (agent steps, skill calls, policy decisions, validation failures) to `.swarmkit/logs/` as JSONL. CLI commands for analysis:

| Command | What it does |
|---|---|
| `swarmkit run --verbose` | Per-agent summary after output (timing, skills, denials) |
| `swarmkit status` | Recent runs at a glance |
| `swarmkit logs` | Detailed events from past runs |
| `swarmkit logs --format markdown` | Compliance-ready audit report |
| `swarmkit run --dry-run` | Show resolved agents + skills without executing |
| `swarmkit why <run-id>` | LLM-powered explanation of what happened |
| `swarmkit ask "question"` | Conversational workspace observer |
| `swarmkit review list` | Pending HITL review items |
| `swarmkit gaps` | Recorded skill gaps |

Per-skill audit control via the `audit:` block in skill YAML:

```yaml
audit:
  log_inputs: summary    # full | summary | none
  log_outputs: full
  redact: ["$.api_key", "$.password"]
```

**Coming in M6-M7** (designed, not yet implemented):

- **OpenTelemetry integration** — trace-per-run, span-per-agent-step with `swarmkit.*` semantic attributes. Console + OTLP/HTTP exporters. Send structural telemetry to any OTel-compatible backend (Jaeger, Grafana, Rynko). See [`design/details/opentelemetry-observability.md`](./design/details/opentelemetry-observability.md).
- **Local ring buffer** — SQLite-backed prompt/response store keyed by OTel span ID. Prompts never leave the user's environment. `swarmkit debug --span-id <id>` retrieves them locally.
- **Intent drift detection** — optional per-agent embedding-based drift scoring. Detects when agents wander from the original goal. Log, warn, or nudge strategies. See [`design/details/intent-drift-detection.md`](./design/details/intent-drift-detection.md).
- **Governance circuit breakers** — `max_steps_per_run`, `max_cost_per_run_usd` — prevent runaway agents.
- **Notification plugins** — webhook-based alerts on HITL requests, errors, skill gaps (Slack, email, generic webhook).

## Reference topologies

The `reference/` directory ships production-quality topologies that any workspace can adopt:

### Code Review Swarm

Three leaders (Engineering, QA, Operations) coordinate a PR review. Engineering fetches the PR via GitHub MCP, analyses code quality and security. QA assesses test coverage. Operations evaluates deployment risk with HITL approval for low-confidence verdicts.

```bash
swarmkit run reference/ code-review --input "Review PR #49 on delivstat/swarmkit"
```

### Skill Authoring Swarm

Six specialist agents (conversation leader, knowledge searcher, schema drafter, validator, test writer, publisher) create and edit SwarmKit artifacts through conversation, grounded by the Knowledge MCP Server.

```bash
swarmkit author skill my-workspace/ --thorough   # uses the authoring swarm
```

### Knowledge Curator (designed, implementation pending)

Maintains a persistent wiki of accumulated knowledge. Three agents: curator (reads sources, writes wiki pages), indexer (builds cross-references and catalogue), linter (checks for staleness, contradictions, gaps). Any workspace can add this topology to build institutional knowledge that persists across conversations.

Three operations:
- **Ingest** — feed a new document, Confluence page, or Jira ticket. Curator creates/updates wiki pages with cross-references.
- **Query-and-persist** — during normal chat, high-quality answers are written to the wiki. Future queries on the same topic find the wiki page first and skip expensive tool chains.
- **Lint** — periodic health check for contradictions, stale content, orphan pages, and missing topics.

Inspired by [Karpathy's LLM-maintained wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). See [`design/details/knowledge-curator-topology.md`](./design/details/knowledge-curator-topology.md) for the full design.

```
reference/
├── workspace.yaml          # MCP servers (GitHub + Knowledge)
├── topologies/
│   ├── code-review.yaml    # 10-agent, 3-level tree
│   ├── skill-authoring.yaml
│   └── knowledge-curator.yaml  # (pending implementation)
├── archetypes/             # 16 reusable agent configs
└── skills/                 # 20 skills (GitHub MCP + decision + knowledge + wiki)
```

## Monorepo layout

```
swarmkit/
├── design/              # Authoritative architecture (v0.6) + per-feature design notes
├── packages/
│   ├── runtime/         # Python: CLI, LangGraph compiler, governance, MCP, knowledge server
│   ├── schema/          # Canonical JSON Schemas + Python & TypeScript validators
│   └── ui/              # Next.js (v1.1)
├── reference/           # Reference topologies, archetypes, skills
├── docker/              # Sandbox images for MCP server isolation
├── examples/            # On-ramp examples (hello-swarm)
├── docs/                # User-facing docs + discipline notes
├── scripts/             # Dev scripts (codegen, demos)
└── llms.txt             # LLM-queryable index (llmstxt.org)
```

## Getting started

### Install from PyPI

```bash
# Recommended — installs as a CLI tool (no venv needed)
uv tool install swarmkit-runtime

# Or with pip in a virtual environment
pip install swarmkit-runtime
```

### Install from source

Prerequisites: Python 3.11+, Node 20+, `pnpm`, `uv`, `just`.

```bash
git clone git@github.com:delivstat/swarmkit.git && cd swarmkit
just install          # uv sync + pnpm install
just test             # 500+ tests across Python + TypeScript
just lint             # ruff + biome
just typecheck        # mypy + tsc
```

### Quick demos

```bash
just demo-schema          # all 5 schemas in both languages
just demo-resolver        # validate + resolve hello-swarm example
just demo-run             # run hello-swarm end-to-end with MCP
just demo-code-review     # Code Review Swarm against a real PR
```

### Try it as an LLM can

SwarmKit docs are designed for LLM consumption. The repo ships [`llms.txt`](./llms.txt) at the root (per [llmstxt.org](https://llmstxt.org)). Or bundle everything:

```bash
swarmkit knowledge-pack -o pack.md    # paste into any LLM
swarmkit knowledge-server             # live MCP server for Claude Code / Cursor
```

## Packages

| Package | Language | Status |
|---|---|---|
| [`swarmkit-runtime`](./packages/runtime) | Python 3.11+ | Active — CLI, compiler, governance, MCP, knowledge server |
| [`swarmkit-schema`](./packages/schema) | Python + TypeScript | Stable — 5 schemas, validators, codegen, drift protection |
| [`swarmkit-ui`](./packages/ui) | TypeScript / Next.js | Scaffolded — v1.1 (web UI extends the CLI, doesn't replace it) |

## Model providers

7 built-in providers, auto-detected from environment variables:

| Provider | Env var | Example model |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| Google | `GOOGLE_API_KEY` | `gemini-2.5-flash` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` |
| OpenRouter | `OPENROUTER_API_KEY` | `meta-llama/llama-3.3-70b-instruct` |
| Groq | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |
| Together | `TOGETHER_API_KEY` | `meta-llama/llama-3.3-70b` |
| Ollama | (always available) | `llama3.3` |

Override per-run: `SWARMKIT_PROVIDER=openrouter SWARMKIT_MODEL=... swarmkit run ...`

## Design principles

From [design doc §7](./design/SwarmKit-Design-v0.6.md):

- **Topology as data, not code.** Swarms are YAML/JSON, interpreted at runtime.
- **Skills as the only extension primitive.** Capability, decision, coordination, persistence — one surface.
- **Framework-aligned, not framework-locked.** LangGraph is the v1.0 engine; the schema is portable.
- **Trust boundaries as first-class concept.** Communication patterns categorised by trust zone.
- **Governance built in, not bolted on.** Separation of Powers model on Microsoft AGT.
- **Growth through human-approved authoring.** Swarms surface gaps; humans decide.
- **Eject, never lock in.** `swarmkit eject` exports standalone LangGraph code.

## License

MIT — see [LICENSE](./LICENSE).
