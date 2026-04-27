# Sterling OMS Project Workspace

A multi-swarm workspace for an IBM Sterling OMS implementation project.
Four topologies, five archetypes, eleven skills, backed by live Sterling
API access and vector-search RAG over product documentation.

## What this gives you

| Swarm | Use it for |
|---|---|
| `solution-review` | Design discussions — architect + retail expert + validator |
| `sterling-qa` | Quick Sterling questions — searches docs + queries live config |
| `code-review` | Review extension code — Sterling patterns + quality + architecture |
| `coding-assistant` | Write extension code — developer + architect guidance |

## Prerequisites

- Python 3.11+ with `uv`
- Node.js 18+ with `npx` (for mcp-local-rag)
- SwarmKit installed (`pip install swarmkit-runtime` or source checkout)
- An OpenRouter API key (`OPENROUTER_API_KEY`)
- A local Sterling OMS instance (for live API access)
- Sterling product documentation (for RAG)

## Setup — step by step

### 1. Set environment variables

```bash
# Model provider
export OPENROUTER_API_KEY=sk-or-...

# Sterling API (your local instance)
export STERLING_API_URL=http://localhost:9080/smcfs/restapi/
export STERLING_API_USER=admin
export STERLING_API_PASSWORD=your_password

# Knowledge base directories
export STERLING_DOCS_DIR=~/sterling-knowledge
export REFERENCE_DESIGNS_DIR=~/sterling-references

# GitHub (for code review topology)
export GITHUB_TOKEN=ghp_...
```

### 2. Prepare the documentation directory

```bash
mkdir -p ~/sterling-knowledge

# Sterling product docs (IBM Knowledge Center)
ln -s /path/to/sterling-infocenter ~/sterling-knowledge/product-docs

# API Javadocs (from Sterling installation)
# Usually at <INSTALL>/repository/eardata/documentation/
ln -s /path/to/sterling/javadocs ~/sterling-knowledge/api-javadocs

# Database ERD (from Sterling installation)
# Usually at <INSTALL>/repository/datatypes/
ln -s /path/to/sterling/erd ~/sterling-knowledge/data-model

# Your project's design docs
ln -s /path/to/project/docs ~/sterling-knowledge/project-docs

# Extension source code
ln -s /path/to/project/extensions/src ~/sterling-knowledge/extensions
```

### 3. Prepare reference designs (optional)

```bash
mkdir -p ~/sterling-references

# Sanitized designs from other Sterling projects
cp -r /path/to/project-alpha/docs ~/sterling-references/project-alpha/
cp -r /path/to/project-beta/docs ~/sterling-references/project-beta/
```

### 4. Ingest documentation into the vector store

First run downloads the embedding model (~90MB). Subsequent runs
are instant (loads existing index).

```bash
cd examples/sterling-oms/workspace

# Ingest product docs + project docs
export STERLING_DOCS_DIR=~/sterling-knowledge
uv run python scripts/ingest-docs.py

# Ingest reference designs (separate index)
STERLING_DOCS_DIR=~/sterling-references uv run python scripts/ingest-docs.py
```

### 5. Validate the workspace

```bash
swarmkit validate examples/sterling-oms/workspace --tree
```

You should see 4 topologies, 11 skills, 5 archetypes.

### 6. Test the Sterling API connection

```bash
swarmkit run examples/sterling-oms/workspace sterling-qa \
  --input "What organizations are configured in our Sterling instance?" \
  --verbose
```

If the Sterling API is accessible, the architect will query
`getOrganizationList` and return the actual organizations.

## Usage

### Solution design discussions

```bash
swarmkit run examples/sterling-oms/workspace solution-review \
  --input "We need ship-from-store for 200 retail locations. \
           What DOM rules do we need and what are the risks?" \
  --verbose
```

### Quick Sterling questions

```bash
swarmkit run examples/sterling-oms/workspace sterling-qa \
  --input "How are our DOM sourcing rules currently configured?" \
  --verbose
```

### Code review

```bash
swarmkit run examples/sterling-oms/workspace code-review \
  --input "Review PR #42 on our-org/sterling-extensions" \
  --verbose
```

### Writing extension code

```bash
swarmkit run examples/sterling-oms/workspace coding-assistant \
  --input "Write a user exit for YFSBeforeCreateOrderUE that \
           validates the shipping address against our address \
           validation service before order creation" \
  --verbose
```

### Observability

```bash
# What happened in the last run?
swarmkit logs examples/sterling-oms/workspace

# Ask the LLM to explain
swarmkit why solution-review-20260427... examples/sterling-oms/workspace

# Quick status
swarmkit status examples/sterling-oms/workspace

# Dry run — see agents + skills without executing
swarmkit run examples/sterling-oms/workspace solution-review --dry-run
```

### HTTP server (persistent mode)

```bash
swarmkit serve examples/sterling-oms/workspace --port 8000

# Then from any client:
curl -X POST http://localhost:8000/run/sterling-qa \
  -H 'Content-Type: application/json' \
  -d '{"input": "What agents are configured in our Sterling instance?"}'
```

## Model configuration

Default setup uses OpenRouter with Qwen3 models:
- Leaders + architects: `qwen/qwen3-235b-a22b` ($0.46/M input)
- Validator: `deepseek/deepseek-chat` ($0.32/M input)
- Workers (if added): `qwen/qwen3-30b-a3b` ($0.08/M input)

Estimated cost: ~$0.02-0.05 per topology run. ~$5-10/month at 20 runs/day.

Override per-run:
```bash
SWARMKIT_PROVIDER=openrouter SWARMKIT_MODEL=moonshotai/kimi-k2 \
  swarmkit run examples/sterling-oms/workspace solution-review --input "..."
```

## Knowledge base architecture

```
┌─────────────────────────────────────────────────────────┐
│ Sterling API MCP Server (live)                          │
│ • get_flow_list, get_sourcing_rule_list, get_agent_list │
│ • Queries Application Manager config directly          │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────┼───────────────────────────────┐
│ mcp-local-rag (sterling-docs)                           │
│ • Product docs (IBM Knowledge Center)                   │
│ • API Javadocs (what the APIs mean)                    │
│ • Database ERD (what the tables/columns mean)          │
│ • Project design docs                                   │
│ • Extension source code                                 │
│ Vector search: hybrid semantic + keyword               │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────┼───────────────────────────────┐
│ mcp-local-rag (reference-designs) — SEPARATE INDEX      │
│ • Sanitized designs from other Sterling projects        │
│ • Agents always label: "In a reference project..."     │
│ • Validator has NO access (prevents hallucination)     │
└─────────────────────────────────────────────────────────┘
```

## Customization

### Add your own topologies

Copy an existing topology and modify. The archetypes and skills
are shared — any topology can reference any archetype.

### Add more skills

```bash
swarmkit author skill examples/sterling-oms/workspace
# Or use the authoring swarm:
swarmkit author skill examples/sterling-oms/workspace --thorough
```

### Edit existing configuration

```bash
swarmkit edit examples/sterling-oms/workspace \
  --input "Add a security reviewer archetype that checks for SQL injection in extensions"
```

## Files

```
workspace/
├── workspace.yaml              # MCP servers + governance + storage
├── sterling_api_server.py      # Live Sterling API MCP server
├── topologies/
│   ├── solution-review.yaml    # Design discussions (3 agents)
│   ├── sterling-qa.yaml        # Q&A (2 agents)
│   ├── code-review.yaml        # Code review (2 agents)
│   └── coding-assistant.yaml   # Write code (2 agents)
├── archetypes/
│   ├── sterling-oms-architect.yaml
│   ├── retail-domain-expert.yaml
│   ├── sterling-config-validator.yaml
│   ├── sterling-code-reviewer.yaml
│   └── sterling-developer.yaml
├── skills/                     # 11 shared skills
│   ├── search-sterling-docs.yaml
│   ├── search-reference-designs.yaml
│   ├── read-context.yaml
│   ├── query-sterling-config.yaml
│   ├── query-sterling-sourcing.yaml
│   ├── call-sterling-api.yaml
│   ├── config-review.yaml
│   ├── business-validation.yaml
│   ├── code-quality-review.yaml
│   ├── github-pr-read.yaml
│   └── github-repo-read.yaml
├── scripts/
│   └── ingest-docs.py          # Vector store ingestion script
└── policies/                   # (empty — for AGT governance when ready)
```
