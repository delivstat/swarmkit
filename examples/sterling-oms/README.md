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

# Knowledge base directories (three separate indexes)
export STERLING_PRODUCT_DOCS_DIR=~/sterling-knowledge      # base product docs
export STERLING_PROJECT_DOCS_DIR=~/sterling-project-docs    # your project files
export REFERENCE_DESIGNS_DIR=~/sterling-references           # other projects

# GitHub (for code review topology)
export GITHUB_TOKEN=ghp_...
```

### 2. Prepare the product documentation directory

This is the stable, base product knowledge — ingested once, never
changes unless you upgrade Sterling versions. Typically 17,000+
files.

```bash
mkdir -p ~/sterling-knowledge

# If you have a large markdown file, split it first:
python scripts/split-markdown.py /path/to/sterling-docs.md \
  --output ~/sterling-knowledge/product-docs/

# API Javadocs (from Sterling installation)
# Usually at <INSTALL>/repository/eardata/documentation/
ln -s /path/to/sterling/javadocs ~/sterling-knowledge/api-javadocs

# Database ERD (from Sterling installation)
# Usually at <INSTALL>/repository/datatypes/
ln -s /path/to/sterling/erd ~/sterling-knowledge/data-model
```

### 3. Prepare the project documentation directory

This is your project-specific knowledge — changes as the project
evolves. Re-ingest when files change.

```bash
mkdir -p ~/sterling-project-docs

# Design documents (Word, PDF, markdown)
ln -s /path/to/project/docs ~/sterling-project-docs/design-docs

# Extension source code (Java)
ln -s /path/to/project/extensions/src ~/sterling-project-docs/extensions

# XSL transforms
ln -s /path/to/project/transforms ~/sterling-project-docs/transforms

# XML templates
ln -s /path/to/project/templates ~/sterling-project-docs/templates

# Integration specs (Excel → convert to markdown first)
pip install openpyxl
python scripts/convert-excel.py /path/to/integration-specs/
mv /path/to/integration-specs/*.md ~/sterling-project-docs/integrations/
```

Supported file types: `.md`, `.txt`, `.html`, `.pdf`, `.docx`,
`.java`, `.xml`, `.xsl`, `.properties`. Excel files must be
converted first (see `scripts/convert-excel.py`). JAR files are
not supported — add their Javadoc/README instead.

### 4. Prepare reference designs (optional)

Designs from other Sterling implementations — separate index so
agents always label citations as reference material, never as
current project fact. The config-validator has no access to this
source.

```bash
mkdir -p ~/sterling-references

# Sanitized designs from other projects (no client names/secrets)
cp -r /path/to/project-alpha/docs ~/sterling-references/project-alpha/
cp -r /path/to/project-beta/docs ~/sterling-references/project-beta/
cp -r /path/to/industry-templates ~/sterling-references/templates/
```

### 5. Ingest documentation into vector stores

First run downloads the embedding model (~90MB, 1-2 min).
Each directory gets its own vector index (LanceDB).

```bash
cd examples/sterling-oms/workspace

# Ingest product docs (run once — 17K files takes hours, run overnight)
STERLING_DOCS_DIR=~/sterling-knowledge \
  nohup uv run python scripts/ingest-docs.py > ingest-product.log 2>&1 &

# Ingest project docs (re-run when project files change)
STERLING_DOCS_DIR=~/sterling-project-docs \
  uv run python scripts/ingest-docs.py

# Ingest reference designs (run once)
STERLING_DOCS_DIR=~/sterling-references \
  uv run python scripts/ingest-docs.py
```

**To reset and start fresh:** delete the `lancedb/` directory
inside each `BASE_DIR` and re-run ingestion:

```bash
rm -rf ~/sterling-knowledge/lancedb/
rm -rf ~/sterling-project-docs/lancedb/
rm -rf ~/sterling-references/lancedb/
```

### 6. Validate the workspace

```bash
swarmkit validate examples/sterling-oms/workspace --tree
```

You should see 5 topologies, 19 skills, 12 archetypes.

### 7. Test the Sterling API connection

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

### Multi-turn conversation (interactive mode)

For design sessions, Q&A, or iterative discussions — context
persists across turns:

```bash
# Start a conversation with the Sterling Q&A topology
swarmkit chat examples/sterling-oms/workspace sterling-qa
> What DOM rules are configured?
[architect queries live config and responds]
> Change the sourcing to cost-based for SFS nodes
[architect responds with context from the previous turn]
> What are the risks of this change?
[architect knows the full conversation history]
exit

# Resume a previous conversation
swarmkit conversations examples/sterling-oms/workspace --pick

# Or resume directly by ID
swarmkit chat examples/sterling-oms/workspace sterling-qa --resume a3f2b1c9
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

Four MCP servers, each with a distinct role:

```
┌─────────────────────────────────────────────────────────┐
│ Sterling API MCP Server (live)                          │
│ • get_flow_list, get_sourcing_rule_list, get_agent_list │
│ • Queries Application Manager config directly           │
│ • Ground truth for "how is our system configured?"      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ mcp-local-rag: sterling-product-docs                    │
│ • 17K+ product docs (IBM Knowledge Center)              │
│ • API Javadocs (what the APIs return + mean)            │
│ • Database ERD (what the tables/columns mean)           │
│ • INGEST ONCE — stable base product knowledge           │
│ • Vector search: hybrid semantic + keyword              │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ mcp-local-rag: sterling-project-docs                    │
│ • Design docs (Word/PDF/markdown)                       │
│ • Extension source code (Java)                          │
│ • XSL transforms                                        │
│ • Integration specs (Excel → markdown)                  │
│ • RE-INGEST when project files change                   │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ mcp-local-rag: reference-designs — SEPARATE INDEX       │
│ • Sanitized designs from other Sterling projects        │
│ • Agents always label: "In a reference project..."      │
│ • Config validator has NO access (prevents hallucination)│
└─────────────────────────────────────────────────────────┘
```

### Which archetype has access to what

| Archetype | Product docs | Project docs | Reference designs | Live API |
|---|---|---|---|---|
| sterling-oms-architect | ✅ | ✅ | ✅ | ✅ |
| retail-domain-expert | ✅ | ✅ | ✅ | — |
| sterling-config-validator | ✅ | ✅ | ❌ (intentional) | ✅ |
| sterling-code-reviewer | ✅ | ✅ | — | — |
| sterling-developer | ✅ | ✅ | — | ✅ |

The config-validator has no reference design access — it validates
against current project reality only. If the architect recommends
something from a reference design that doesn't fit, the validator
catches it because it only sees the current configuration.

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
├── workspace.yaml              # 4 MCP servers + governance + storage
├── sterling_api_server.py      # Live Sterling API MCP server (10 tools)
├── topologies/
│   ├── solution-review.yaml    # Design discussions (3 agents)
│   ├── sterling-qa.yaml        # Q&A (2 agents)
│   ├── code-review.yaml        # Code review (2 agents)
│   ├── coding-assistant.yaml   # Write code (2 agents)
│   └── skill-authoring.yaml    # Multi-agent authoring (6 agents)
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
