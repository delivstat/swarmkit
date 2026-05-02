# Sterling OMS Project Workspace

A multi-swarm workspace for an IBM Sterling OMS implementation project.
Five topologies, five archetypes, nineteen skills, backed by CDT config
access, structured API javadocs, and vector-search RAG over product documentation.

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
- Node.js 18+ with `npx` (for GitHub, filesystem MCP servers)
- SwarmKit installed (`pip install swarmkit-runtime` or source checkout)
- An OpenRouter API key (`OPENROUTER_API_KEY`)
- Sterling CDT XML dump (for config access) and/or product documentation (for RAG)

## Setup — step by step

### 1. Run the setup script

```bash
cd examples/sterling-oms/workspace

# Creates all directories + generates .env file
./scripts/setup-knowledge.sh                    # uses ~/sterling-knowledge
./scripts/setup-knowledge.sh /data/sterling     # or custom base directory
```

This creates the following directory structure:

```
~/sterling-knowledge/
├── product-docs/              (indexed in ChromaDB)
│   ├── product-docs/        ← Sterling product docs (markdown/HTML)
│   ├── api-reference/       ← API summaries (exported from javadocs server)
│   ├── data-model/          ← Entity XMLs → markdown
│   ├── core-javadocs/       ← Core/baseutils javadocs → markdown
│   └── erd/                 ← Database ERD HTML files
│
├── api-javadocs/             (dedicated MCP server — NOT indexed in RAG)
│
├── project-docs/             (indexed in ChromaDB)
│   ├── design-docs/         ← Project Word/PDF/markdown docs
│   ├── integrations/        ← Integration specs (Excel → markdown)
│   └── 3rd-party-docs/      ← README/Javadoc for key libraries
│
├── project-code/             (NOT indexed — developer reads directly)
│   ├── extensions/          ← Java extension source code
│   ├── transforms/          ← XSL transform files
│   └── templates/           ← XML API/config templates
│
├── references/               (separate ChromaDB index)
│   └── templates/           ← Reusable patterns from other projects
│
└── notes/                    (agents write analysis here — not git tracked)
```

### 2. Edit .env with your credentials

```bash
nano .env   # or: source .env after editing
```

Key variables:
```bash
OPENROUTER_API_KEY=sk-or-...
STERLING_CDT_DIR=/path/to/CDT-dump/        # raw CDT XML files
GITHUB_TOKEN=ghp_...
```

### 3. Copy/symlink your source files

```bash
source .env

# Product docs (markdown/HTML from IBM Knowledge Center)
cp -r /path/to/product-docs/* $STERLING_PRODUCT_DOCS_DIR/product-docs/

# API Javadocs (for the dedicated MCP server)
ln -s /path/to/api_javadocs $STERLING_JAVADOCS_DIR/api_javadocs

# Database ERD HTMLs
ln -s /path/to/erd $STERLING_PRODUCT_DOCS_DIR/erd

# Project code
ln -s /path/to/extensions/src $STERLING_PROJECT_CODE_DIR/extensions
ln -s /path/to/transforms $STERLING_PROJECT_CODE_DIR/transforms
ln -s /path/to/templates $STERLING_PROJECT_CODE_DIR/templates

# Project design docs
ln -s /path/to/project/docs $STERLING_PROJECT_DOCS_DIR/design-docs
```

### 4. Convert and prepare data

```bash
# Split large markdown files
python scripts/split-markdown.py /path/to/docs.md \
  --output $STERLING_PRODUCT_DOCS_DIR/product-docs/

# Convert entity XMLs to markdown
python scripts/convert-entity-xml.py /path/to/entity-xmls/ \
  --datatypes /path/to/datatypes.xml \
  --output $STERLING_PRODUCT_DOCS_DIR/data-model/

# Convert core/baseutils javadocs to markdown
python scripts/convert-javadocs.py /path/to/core_javadocs/ \
  --output $STERLING_PRODUCT_DOCS_DIR/core-javadocs/
python scripts/convert-javadocs.py /path/to/baseutils_doc/ \
  --output $STERLING_PRODUCT_DOCS_DIR/core-javadocs/

# Export API summaries for RAG discovery
STERLING_JAVADOCS_DIR=$STERLING_JAVADOCS_DIR/api_javadocs \
  uv run sterling_javadocs_server.py \
  --export-summaries $STERLING_PRODUCT_DOCS_DIR/api-reference/

# Convert Excel integration specs to markdown
pip install openpyxl
python scripts/convert-excel.py /path/to/excel-files/
mv /path/to/excel-files/*.md $STERLING_PROJECT_DOCS_DIR/integrations/
```

### 5. Ingest CDT config dump

```bash
# Parse CDT XML files into structured JSON index
python scripts/ingest-cdt.py /path/to/CDT-directory/ \
  --output $STERLING_CDT_INDEX

# Indexes: 1542 services, 101 pipelines, 851 transactions,
# 985 events, 527 statuses, 27 hold types, 2854 common codes
```

### 6. Prepare reference designs (optional)

```bash
# Sanitized designs from other projects (no client names/secrets)
cp -r /path/to/project-alpha/docs $REFERENCE_DESIGNS_DIR/project-alpha/
cp -r /path/to/industry-templates $REFERENCE_DESIGNS_DIR/templates/
```

### 7. Ingest everything

The master ingestion script runs all applicable ingestions in parallel:

```bash
# Run all ingestions (checks which env vars are set)
python scripts/ingest-all.py

# Reset indexes and re-ingest from scratch
python scripts/ingest-all.py --reset

# Run just one ingestion
python scripts/ingest-all.py --only cdt

# See what would run
python scripts/ingest-all.py --list
```

Or run individual ingestions manually:

```bash
# CDT config dump → structured JSON
python scripts/ingest-cdt.py /path/to/CDT/ --output $STERLING_CDT_INDEX

# Product docs → ChromaDB + FTS5 (~30 min for 20K files)
STERLING_DOCS_DIR=$STERLING_PRODUCT_DOCS_DIR uv run scripts/ingest-docs.py

# Project docs → ChromaDB + FTS5
STERLING_DOCS_DIR=$STERLING_PROJECT_DOCS_DIR uv run scripts/ingest-docs.py

# Reference designs → ChromaDB + FTS5
STERLING_DOCS_DIR=$REFERENCE_DESIGNS_DIR uv run scripts/ingest-docs.py

# Reset and re-ingest from scratch
STERLING_DOCS_DIR=$STERLING_PRODUCT_DOCS_DIR uv run scripts/ingest-docs.py --reset
```

### 8. Build code knowledge graph (optional)

```bash
uvx --from graphifyy graphify update $STERLING_PROJECT_CODE_DIR
# Outputs: $STERLING_PROJECT_CODE_DIR/graphify-out/graph.json
# (4818 nodes, 16162 edges from 1209 files — takes ~30 seconds)
```

### 9. Validate and run

```bash
swarmkit validate examples/sterling-oms/workspace --tree
# Should see: 5 topologies, 19 skills, 12 archetypes

swarmkit chat . sterling-qa
```

### 10. Test it

```bash
swarmkit run examples/sterling-oms/workspace sterling-qa \
  --input "What services are configured for order creation?" \
  --verbose
```

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

### Dynamic model switching in chat

Switch the LLM model on the fly during a conversation:

```bash
swarmkit chat examples/sterling-oms/workspace sterling-assistant
> which API modifies an order?
[response with default models]

> /model deepseek/deepseek-chat
Switched to: deepseek/deepseek-chat (via openrouter)

> same question
[response with deepseek — compare quality]

> /model google/gemini-2.5-flash
Switched to: google/gemini-2.5-flash (via openrouter)

> /model
Current model: google/gemini-2.5-flash (provider: openrouter)

> /model reset
Model reset to topology defaults.
```

Any model available on OpenRouter works — just use the
`provider/model` format from the [OpenRouter models page](https://openrouter.ai/models).

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

Default setup uses OpenRouter:
- Root (router): `meta-llama/llama-3.3-70b-instruct` ($0.10/M)
- Workers: `deepseek/deepseek-chat` ($0.32/M)
- Validator: `deepseek/deepseek-chat` ($0.32/M)

Estimated cost: ~$0.05 per run. ~$30/month at active usage.

Override per-run or dynamically in chat:
```bash
SWARMKIT_MODEL=deepseek/deepseek-chat \
  swarmkit run . sterling-assistant --input "..."

# Or in chat mode:
> /model deepseek/deepseek-chat
> /model reset
```

## Knowledge base architecture

Nine MCP servers, each with a distinct role:

```
┌─────────────────────────────────────────────────────────┐
│ Sterling CDT Config Server (from CDT XML dump)          │
│ • get_service_config, render_service_graph, list_services│
│ • get_pipeline, render_pipeline_graph, get_transactions │
│ • get_events, get_user_exits, get_hold_types            │
│ • get_common_codes, search_configs, get_config_table    │
│ • No live Sterling instance needed                      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ chroma-mcp-server: sterling-product-docs (ChromaDB, ONNX)       │
│ • 17K+ product docs (IBM Knowledge Center)              │
│ • API Javadocs (what the APIs return + mean)            │
│ • Database ERD (what the tables/columns mean)           │
│ • INGEST ONCE — stable base product knowledge           │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ chroma-mcp-server: sterling-project-docs                          │
│ • Design docs (Word/PDF/markdown)                       │
│ • Integration specs (Excel → markdown)                  │
│ • RE-INGEST when project docs change                    │
│ • Code is NOT here — developer reads it directly        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ chroma-mcp-server: reference-designs — SEPARATE INDEX             │
│ • Sanitized designs from other Sterling projects        │
│ • Agents always label: "In a reference project..."      │
│ • Config validator has NO access (prevents hallucination)│
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Sterling API Javadocs MCP Server (structured, 1006 APIs)│
│ • search_apis, get_api_details, get_api_input_xml       │
│ • get_api_output_xml, get_api_user_exits, get_api_events│
│ • Input/output samples (XML + JSON), DTDs               │
│ • Precise structured data — not fuzzy RAG search        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Graphify Code Graph (optional, token-efficient)         │
│ • query-code-graph: find related classes, call chains   │
│ • explain-code-path: trace how components connect       │
│ • ~2K tokens per query vs ~50K for raw file reads       │
│ • Build: graphify update $PROJECT_CODE (~30 seconds)     │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ GitHub MCP Server (code access)                         │
│ • Developer agent reads .java, .xsl, .xml directly      │
│ • Full file context, not vector-search fragments         │
│ • Code review reads PRs + diffs                         │
└─────────────────────────────────────────────────────────┘
```

### Which archetype has access to what

| Archetype | Product docs | Project docs | Reference designs | API Javadocs | CDT Config | Code | Code Graph |
|---|---|---|---|---|---|---|---|
| sterling-oms-architect | ✅ | ✅ | ✅ | ✅ | ✅ | — | — |
| retail-domain-expert | ✅ | ✅ | ✅ | — | — | — | — |
| sterling-config-validator | ✅ | ✅ | ❌ (intentional) | — | ✅ | — | — |
| sterling-code-reviewer | ✅ | ✅ | — | ✅ | — | ✅ | ✅ |
| sterling-developer | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ |

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
├── sterling_cdt_server.py      # CDT config MCP server (14 tools, services/pipelines/events)
├── sterling_javadocs_server.py # API Javadocs MCP server (1006 APIs, 10 tools)
├── fts_server.py              # Full-text search MCP server (SQLite FTS5)
├── graphify_server.py         # Code knowledge graph MCP wrapper (Graphify CLI)
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
│   ├── get-service-config.yaml
│   ├── query-sterling-sourcing.yaml
│   ├── call-sterling-api.yaml
│   ├── config-review.yaml
│   ├── business-validation.yaml
│   ├── code-quality-review.yaml
│   ├── github-pr-read.yaml
│   └── github-repo-read.yaml
├── scripts/
│   ├── ingest-all.py           # Master script — runs all ingestions in parallel
│   ├── ingest-docs.py          # Batch ingestion into ChromaDB + FTS5 (ONNX embeddings)
│   ├── ingest-cdt.py           # CDT XML dumps → structured JSON indexes
│   ├── setup-knowledge.sh      # Create knowledge directories + .env file
│   ├── convert-entity-xml.py   # Sterling entity XMLs → consolidated markdown (merges by TableName)
│   ├── convert-javadocs.py     # Core/baseutils Javadoc HTML → markdown for RAG
│   ├── convert-excel.py        # Excel integration specs → markdown tables
│   ├── split-markdown.py       # Split large markdown files on ## headings
│   └── scrape-ibm-docs.py      # Scrape IBM docs site with Playwright (offline indexing)
└── policies/                   # (empty — for AGT governance when ready)
```
