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
| `sterling-assistant` | General-purpose — routes to architect or developer, cross-consults |

## Prerequisites

- Python 3.11+ with `uv`
- Node.js 18+ with `npx` (for GitHub MCP server)
- SwarmKit installed (`pip install swarmkit-runtime` or source checkout)
- `rag-mcp` installed (`pip install rag-mcp`)
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

# Knowledge base directories (three separate indexes — documentation only)
export STERLING_PRODUCT_DOCS_DIR=~/sterling-knowledge      # base product docs
export STERLING_PROJECT_DOCS_DIR=~/sterling-project-docs    # your project docs
export REFERENCE_DESIGNS_DIR=~/sterling-references           # other projects

# Project code (developer agent reads directly — NOT indexed in RAG)
export STERLING_PROJECT_CODE_DIR=~/sterling-project-code

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

This is your project-specific **documentation** — changes as the
project evolves. Re-ingest when files change.

Code files (Java extensions, XSL transforms, XML templates) are
**not** indexed here. The developer agent reads those directly
from the filesystem/GitHub — it needs the full file structure,
not vector-search fragments.

```bash
mkdir -p ~/sterling-project-docs

# Design documents (Word, PDF, markdown)
ln -s /path/to/project/docs ~/sterling-project-docs/design-docs

# Integration specs (Excel → convert to markdown first)
pip install openpyxl
python scripts/convert-excel.py /path/to/integration-specs/
mv /path/to/integration-specs/*.md ~/sterling-project-docs/integrations/

# 3rd-party library docs
cp /path/to/library-readme.md ~/sterling-project-docs/3rd-party-docs/
```

Supported file types for RAG: `.md`, `.html`, `.pdf`, `.docx`.
Excel files must be converted first (see `scripts/convert-excel.py`).
Entity XMLs must be converted first (see `scripts/convert-entity-xml.py`).
Code files (`.java`, `.xml`, `.xsl`) belong in your repo — the
developer agent reads them via the filesystem MCP server.

```bash
# Convert Sterling entity XMLs to markdown (product or custom)
# --datatypes resolves DataType names to actual DB types (Key → NCHAR(24))
python scripts/convert-entity-xml.py /path/to/entity-xmls/ \
  --datatypes /path/to/datatypes.xml \
  --output ~/sterling-knowledge/data-model/

# Run product entities first, then custom — extensions append to existing files
python scripts/convert-entity-xml.py /path/to/omp_tables.xml \
  --datatypes /path/to/datatypes.xml \
  --output ~/sterling-knowledge/data-model/
python scripts/convert-entity-xml.py /path/to/custom-entities/ \
  --datatypes /path/to/datatypes.xml \
  --output ~/sterling-knowledge/data-model/
```

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

First run downloads the embedding model (~500MB). Each directory
gets its own ChromaDB collection (stored in `~/.local/share/chroma/`).

```bash
pip install rag-mcp  # one-time

cd examples/sterling-oms/workspace

# Ingest product docs (run once — 17K files takes hours, run overnight)
STERLING_DOCS_DIR=~/sterling-knowledge/product-docs \
  nohup python scripts/ingest-docs.py > ingest-product.log 2>&1 &

# Ingest project docs (re-run when project files change)
STERLING_DOCS_DIR=~/sterling-project-docs \
  python scripts/ingest-docs.py

# Ingest reference designs (run once)
STERLING_DOCS_DIR=~/sterling-references \
  python scripts/ingest-docs.py
```

Only documentation files are indexed (`.md`, `.html`, `.pdf`, `.docx`).
Code files (`.java`, `.xml`, `.xsl`) are **not** indexed — the
developer agent reads those directly from the repo.

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

### General-purpose assistant (routes + cross-consults)

```bash
# Design question — architect answers, consults developer for current state
swarmkit chat examples/sterling-oms/workspace sterling-assistant
> How should we implement ship-from-store inventory checks?
[root → developer: "what does current inventory integration look like?"]
[root → architect: designs solution with awareness of current code]

# Code question — developer answers, consults architect for design intent
> Show me how the CreateOrder user exit works
[root → developer: reads code, explains implementation]

# Mixed question — both agents consulted
> The address validation UE is throwing NPE in production, how do we fix it?
[root → developer: reads code to identify the bug]
[root → architect: checks design for correct pattern]
[root: synthesises fix + architectural recommendation]
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

Five MCP servers, each with a distinct role:

```
┌─────────────────────────────────────────────────────────┐
│ Sterling API MCP Server (live)                          │
│ • get_flow_list, get_sourcing_rule_list, get_agent_list │
│ • Queries Application Manager config directly           │
│ • Ground truth for "how is our system configured?"      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ rag-mcp: sterling-product-docs (Python, ChromaDB)       │
│ • 17K+ product docs (IBM Knowledge Center)              │
│ • API Javadocs (what the APIs return + mean)            │
│ • Database ERD (what the tables/columns mean)           │
│ • INGEST ONCE — stable base product knowledge           │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ rag-mcp: sterling-project-docs                          │
│ • Design docs (Word/PDF/markdown)                       │
│ • Integration specs (Excel → markdown)                  │
│ • RE-INGEST when project docs change                    │
│ • Code is NOT here — developer reads it directly        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ rag-mcp: reference-designs — SEPARATE INDEX             │
│ • Sanitized designs from other Sterling projects        │
│ • Agents always label: "In a reference project..."      │
│ • Config validator has NO access (prevents hallucination)│
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ GitHub MCP Server (code access)                         │
│ • Developer agent reads .java, .xsl, .xml directly      │
│ • Full file context, not vector-search fragments         │
│ • Code review reads PRs + diffs                         │
└─────────────────────────────────────────────────────────┘
```

### Which archetype has access to what

| Archetype | Product docs | Project docs | Reference designs | Live API | Code (GitHub) |
|---|---|---|---|---|---|
| sterling-oms-architect | ✅ | ✅ | ✅ | ✅ | — |
| retail-domain-expert | ✅ | ✅ | ✅ | — | — |
| sterling-config-validator | ✅ | ✅ | ❌ (intentional) | ✅ | — |
| sterling-code-reviewer | ✅ | ✅ | — | — | ✅ |
| sterling-developer | ✅ | ✅ | — | ✅ | ✅ |

**Key design decisions:**
- The config-validator has no reference design access — it validates
  against current project reality only.
- Code files (Java, XSL, XML templates) are **not** in the RAG index.
  The developer and code-reviewer agents read them directly via GitHub,
  preserving full file structure, imports, and class hierarchy.

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
├── skills/                     # 19 shared skills
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
│   ├── ingest-docs.py          # Vector store ingestion (rag-mcp, pure Python)
│   ├── setup-knowledge.sh      # Create knowledge directories + .env file
│   ├── convert-entity-xml.py   # Sterling entity XMLs → markdown (with datatypes.xml resolution)
│   ├── convert-excel.py        # Excel integration specs → markdown tables
│   └── split-markdown.py       # Split large markdown files on ## headings
└── policies/                   # (empty — for AGT governance when ready)
```
