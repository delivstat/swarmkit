---
title: Building a Sterling OMS Agent Workspace — step-by-step guide
description: How to create a SwarmKit workspace with IBM Sterling OMS and retail domain archetypes, backed by a project-specific knowledge base.
---

# Building a Sterling OMS Agent Workspace

This guide walks through creating a SwarmKit workspace for an IBM
Sterling OMS project — from archetypes and skills to a knowledge
base that grounds the agents in your actual project configuration.

## Prerequisites

- SwarmKit installed (`pip install swarmkit-runtime` or source checkout)
- A model provider configured (e.g. `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`)
- Your Sterling OMS project files accessible on disk:
  - Product documentation (IBM InfoCenter HTML/PDF or your team's docs)
  - Project configuration XMLs (from `/smcfs/` or your config repo)
  - Extension code (Java source)
  - Integration specs (API contracts, EDI mappings)

## Step 1 — Create the workspace

```bash
mkdir sterling-oms-swarm && cd sterling-oms-swarm
swarmkit init .
```

When the authoring agent asks what the swarm should do, describe:

> "I need an agent swarm for an IBM Sterling OMS implementation
> project. The swarm should have a Sterling OMS Solution Architect
> who understands order management configuration, DOM rules, agents,
> pipelines, and the extension framework. It should also have a
> Retail Industry Expert who understands omnichannel fulfillment,
> inventory management, and retail business processes. The agents
> need access to our project's Sterling configuration files and
> documentation."

The authoring agent will generate workspace.yaml, a topology, and
initial archetypes. Review what it generates — you'll likely want
to refine the prompts using the detailed versions below.

## Step 2 — Refine the archetypes

The authoring agent's initial archetypes will be generic. Replace
or edit them with these detailed versions.

### Sterling OMS Solution Architect

Create `archetypes/sterling-oms-architect.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: sterling-oms-architect
  name: Sterling OMS Solution Architect
  description: >
    IBM Sterling OMS expert with deep knowledge of order management
    configuration, fulfillment orchestration, DOM rules, agent/pipeline
    configuration, the extension framework, and integration patterns.
    Grounds all recommendations in the project's actual configuration.
role: worker
defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4-6
    temperature: 0.2
  prompt:
    system: |
      You are a senior IBM Sterling OMS Solution Architect with 10+
      years of hands-on implementation experience. Your expertise covers:

      ORDER MANAGEMENT:
      - Order capture, modification, scheduling, and release
      - Order line types (product, service, delivery, provided service)
      - Order statuses and status pipeline configuration
      - Hold types, hold processing, and approval workflows
      - Order purge strategies and archival

      DISTRIBUTED ORDER MANAGEMENT (DOM):
      - Sourcing rules and rule sequences
      - Availability checking (ATP, future inventory, safety stock)
      - Cost-based and priority-based sourcing optimization
      - Promising rules and delivery date calculations
      - Node capacity constraints and zone-based sourcing

      FULFILLMENT:
      - Shipment creation, consolidation, and release
      - Wave management and pick/pack/ship processes
      - Ship-from-store, BOPIS, and curbside pickup flows
      - Drop-ship and marketplace fulfillment
      - Returns processing (RMA, in-store returns, exchanges)

      CONFIGURATION:
      - Agent and integration server configuration
      - Pipeline and transaction processing
      - Condition builder and custom conditions
      - Event management and alert configuration
      - Document types and process type definitions
      - Organization and participant modeling

      EXTENSION FRAMEWORK:
      - User Exit (YFSxxxUE) implementation patterns
      - Custom API development and override APIs
      - Extended database columns and custom tables
      - SIF (Sterling Integration Framework) message flows
      - Business rule extensibility

      DATABASE:
      - Core schema knowledge (YFS_ORDER_HEADER, YFS_ORDER_LINE,
        YFS_SHIPMENT, YFS_INVENTORY_ITEM, YFS_ITEM, etc.)
      - Query optimization for Sterling tables
      - Transaction volume considerations

      INTEGRATION:
      - Inbound/outbound API patterns (XML over HTTP, JMS, file)
      - Sterling B2B integration
      - ERP integration patterns (SAP, Oracle)
      - E-commerce platform integration
      - WMS/TMS integration patterns

      APPROACH:
      - Always search the project knowledge base before answering
      - Reference specific Sterling APIs, tables, or config elements
      - When suggesting configuration changes, specify the exact XML
        elements and attribute values
      - Flag risks and performance implications of recommendations
      - Cite Sterling documentation sections when relevant
      - Distinguish between out-of-box features and custom extensions

      KNOWLEDGE SOURCES — CRITICAL:
      - search-project: YOUR project's actual config. Ground truth.
        Use for "how does our system work?" questions.
      - search-reference-designs: Other Sterling implementations.
        Inspiration only. Always label: "In a reference project..."
        Never state reference patterns as current project fact.
      - When recommending changes: state what currently exists FIRST
        (from project), then what the reference shows, then your
        recommendation with specific steps to adapt.
  skills:
    - search-project
    - search-reference-designs
    - read-context
    - query-swarmkit-docs
  iam:
    base_scope: [knowledge:read]
provenance:
  authored_by: human
  version: 1.0.0
```

### Retail Industry Expert

Create `archetypes/retail-domain-expert.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: retail-domain-expert
  name: Retail Industry Expert
  description: >
    Retail domain expert specializing in omnichannel order management,
    inventory strategies, fulfillment optimization, and industry
    best practices. Bridges business requirements to technical
    implementation.
role: worker
defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4-6
    temperature: 0.3
  prompt:
    system: |
      You are a retail industry domain expert with 15+ years of
      experience in omnichannel retail operations. Your expertise:

      OMNICHANNEL FULFILLMENT:
      - Buy Online Pick Up In Store (BOPIS / Click & Collect)
      - Ship From Store (SFS) and store-as-warehouse models
      - Drop-ship and marketplace fulfillment
      - Same-day and next-day delivery strategies
      - Curbside pickup operations
      - Mixed-cart fulfillment (split shipments vs. consolidation)

      INVENTORY MANAGEMENT:
      - Available to Promise (ATP) and allocation strategies
      - Safety stock and buffer inventory policies
      - Inventory segmentation (selling channels, fulfillment types)
      - Demand sensing and inventory positioning
      - Inventory accuracy and cycle counting
      - Endless aisle and virtual inventory concepts

      ORDER PROMISING AND SOURCING:
      - Delivery date promising (committed vs. estimated)
      - Cost-to-serve optimization (shipping cost, labor, distance)
      - Node priority and capacity-based sourcing
      - Zone-based fulfillment strategies
      - Backorder management and customer communication
      - Order modification and cancellation policies

      RETURNS AND REVERSE LOGISTICS:
      - Omnichannel returns (buy online return in store, BORIS)
      - Return merchandise authorization (RMA) workflows
      - Exchange processing and instant credit
      - Return disposition and restocking
      - Fraud prevention in returns

      RETAIL OPERATIONS:
      - Store operations and order management at the store level
      - Customer service and call center operations
      - Retail KPIs: fill rate, OTIF, order cycle time, cost per order
      - Seasonal demand planning and peak operations
      - Labor optimization for fulfillment operations

      INDUSTRY STANDARDS:
      - GS1 standards (GTIN, SSCC, GLN)
      - EDI transaction sets (850, 855, 856, 810, 860)
      - Retail compliance and vendor requirements
      - PCI-DSS considerations for order management

      APPROACH:
      - Frame technical questions in business terms
      - Quantify impact when possible (cost savings, time reduction)
      - Reference industry benchmarks and best practices
      - Consider the customer experience impact of every decision
      - Flag when a business requirement conflicts with system
        capabilities or industry norms
      - Suggest phased rollout approaches for complex changes

      KNOWLEDGE SOURCES — CRITICAL:
      - search-project: YOUR project's actual state. Ground truth.
      - search-reference-designs: Other implementations. Inspiration
        only. Always label: "A reference retailer handled this by..."
        Never present reference patterns as current project decisions.
  skills:
    - search-project
    - search-reference-designs
    - read-context
  iam:
    base_scope: [knowledge:read]
provenance:
  authored_by: human
  version: 1.0.0
```

## Step 3 — Set up the knowledge base

This is the most impactful step. Without it, the agents rely only
on the LLM's general training data. With it, they can search your
actual project files.

### Setting up mcp-local-rag (recommended for large doc sets)

[mcp-local-rag](https://github.com/shinpr/mcp-local-rag) provides
semantic search with keyword boosting over local files. Zero install
(runs via `npx`), handles PDF/DOCX/TXT/MD/HTML, embeddings run
locally on CPU, no external API calls. Perfect for the Sterling
InfoCenter documentation (hundreds of MB of HTML).

#### Step 3.1 — Prepare the docs directory

Organize your Sterling project knowledge into a single directory
tree. The server indexes everything under `BASE_DIR`:

```bash
mkdir -p ~/sterling-knowledge

# Copy or symlink your docs
ln -s /path/to/sterling-infocenter ~/sterling-knowledge/product-docs
ln -s /path/to/project/config ~/sterling-knowledge/project-config
ln -s /path/to/project/extensions/src ~/sterling-knowledge/extensions
ln -s /path/to/project/docs ~/sterling-knowledge/project-docs
```

The directory structure should look like:

```
~/sterling-knowledge/
├── product-docs/          # IBM Sterling InfoCenter HTML/PDF
│   ├── order-management/
│   ├── dom-configuration/
│   ├── api-reference/
│   └── ...
├── project-config/        # Your Sterling XML configs
│   ├── agents/
│   ├── pipelines/
│   ├── dom-rules/
│   └── ...
├── extensions/            # Java extension source code
│   └── com/yourco/sterling/
└── project-docs/          # Design docs, integration specs
    ├── architecture.md
    ├── integration-specs/
    └── ...
```

#### Step 3.2 — Verify mcp-local-rag works

Test it standalone first (requires Node.js 18+):

```bash
# First run downloads the embedding model (~90MB, takes 1-2 min)
BASE_DIR=~/sterling-knowledge npx -y mcp-local-rag
```

This starts the MCP server on stdio. Press Ctrl+C to stop.

#### Step 3.3 — Wire it into workspace.yaml

Add the server to your workspace config:

```yaml
mcp_servers:
  - id: sterling-knowledge
    transport: stdio
    command: ["npx", "-y", "mcp-local-rag"]
    env:
      BASE_DIR: "${STERLING_KNOWLEDGE_DIR}"
```

Set the env var before running:

```bash
export STERLING_KNOWLEDGE_DIR=~/sterling-knowledge
```

#### Step 3.4 — Ingest the documentation

The server doesn't auto-index on startup — you need to ingest
files explicitly. The fastest way: write a small ingestion script
that calls the `ingest_file` tool for each file.

Create `scripts/ingest-docs.py` in your workspace:

```python
"""Ingest Sterling documentation into mcp-local-rag."""

import asyncio
import os
from pathlib import Path

from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp import ClientSession


async def main() -> None:
    base_dir = os.environ.get(
        "STERLING_KNOWLEDGE_DIR", os.path.expanduser("~/sterling-knowledge")
    )
    params = StdioServerParameters(
        command="npx",
        args=["-y", "mcp-local-rag"],
        env={**os.environ, "BASE_DIR": base_dir},
    )

    async with stdio_client(params) as transport:
        async with ClientSession(*transport) as session:
            await session.initialize()

            # Get current status
            result = await session.call_tool("status", {})
            for block in result.content:
                print(getattr(block, "text", ""))

            # Find all files to ingest
            root = Path(base_dir)
            extensions = {
                ".html", ".htm", ".md", ".txt",
                ".xml", ".properties", ".java",
                ".pdf", ".docx",
            }
            files = [
                f for f in root.rglob("*")
                if f.is_file()
                and f.suffix.lower() in extensions
                and f.stat().st_size < 10_000_000  # skip files > 10MB
            ]

            print(f"\nFound {len(files)} files to ingest.")

            for i, file_path in enumerate(files, 1):
                rel = file_path.relative_to(root)
                try:
                    result = await session.call_tool(
                        "ingest_file",
                        {"filePath": str(file_path)},
                    )
                    status = "ok"
                    for block in result.content:
                        text = getattr(block, "text", "")
                        if "error" in text.lower():
                            status = "error"
                except Exception as e:
                    status = f"failed: {e}"

                if i % 50 == 0 or status != "ok":
                    print(f"  [{i}/{len(files)}] {rel} — {status}")

            # Final status
            print("\nIngestion complete.")
            result = await session.call_tool("status", {})
            for block in result.content:
                print(getattr(block, "text", ""))


if __name__ == "__main__":
    asyncio.run(main())
```

Run the ingestion:

```bash
export STERLING_KNOWLEDGE_DIR=~/sterling-knowledge
uv run python scripts/ingest-docs.py
```

This runs once — after ingestion, the vector database is persisted
in `./lancedb/` inside the `BASE_DIR`. Subsequent server starts
load the existing index instantly.

**Re-ingestion:** when you update docs or config, run the script
again. Updated files are automatically replaced in the index.

#### Step 3.5 — Test the search

Verify the agents can search your docs:

```bash
# Quick test via Python
uv run python -c "
import asyncio, os
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp import ClientSession

async def main():
    params = StdioServerParameters(
        command='npx', args=['-y', 'mcp-local-rag'],
        env={**os.environ, 'BASE_DIR': os.environ['STERLING_KNOWLEDGE_DIR']},
    )
    async with stdio_client(params) as transport:
        async with ClientSession(*transport) as session:
            await session.initialize()
            # Search for DOM rules
            result = await session.call_tool(
                'query_documents',
                {'query': 'DOM sourcing rules configuration', 'limit': 3},
            )
            for block in result.content:
                text = getattr(block, 'text', '')
                print(text[:200])
                print('---')

asyncio.run(main())
"
```

You should see relevant Sterling DOM configuration content from
your ingested docs.

#### Step 3.6 — MCP tools available to your agents

mcp-local-rag exposes 7 tools. The ones your agents will use:

| Tool | What it does | When agents use it |
|---|---|---|
| `query_documents` | Semantic search with keyword boost. Params: `query` (string), `limit` (1-20, default 10). Returns ranked chunks with file path and content. | Before answering any Sterling question — search first, answer second. |
| `read_chunk_neighbors` | Expand context around a search result. Params: `filePath`, `chunkIndex`, `neighbors` (default 2). | When a search hit needs more surrounding context (e.g., the full XML element, not just the matched chunk). |
| `list_files` | List all ingested files with status. | Diagnostic — check what's indexed. |
| `ingest_file` | Add/update a file in the index. Params: `filePath`. | When the agent notices a referenced file isn't in the index. |
| `status` | Database health and stats. | Diagnostic. |

#### Alternative: gnosis-mcp (if you prefer SQLite over LanceDB)

[gnosis-mcp](https://github.com/nicobailon/gnosis-mcp) uses SQLite
FTS5 for full-text search and optionally pgvector for semantic
search. Simpler stack (no Node.js, pure Python), but less
sophisticated retrieval than mcp-local-rag's hybrid approach.

```bash
pip install gnosis-mcp
gnosis-mcp --db ~/sterling-knowledge/gnosis.db \
           --docs ~/sterling-knowledge/
```

Use mcp-local-rag for the best retrieval quality. Use gnosis-mcp
if you want to stay Python-only.

### Adding reference designs from other projects

Reference designs from past Sterling implementations are valuable
for discovering patterns, solutions, and scenarios you might not
have considered. But they create a hallucination risk: the agent
might cite a reference project's configuration as if it exists in
your current project.

The fix: **separate knowledge sources with explicit labeling**.

#### Step 3.7 — Prepare the reference designs directory

Create a separate directory for reference materials — completely
isolated from your current project files:

```bash
mkdir -p ~/sterling-references

# Copy sanitized designs from other projects
cp -r /path/to/project-alpha/docs ~/sterling-references/project-alpha/
cp -r /path/to/project-beta/docs ~/sterling-references/project-beta/
cp -r /path/to/industry-templates ~/sterling-references/templates/
```

What to include:
- Solution design documents (sanitized — no client names or secrets)
- DOM rule configurations that solved specific business problems
- Extension patterns (user exit implementations, custom APIs)
- Integration architecture diagrams and specs
- Performance tuning configurations
- Test scenarios and edge case documentation

```
~/sterling-references/
├── project-alpha/          # Grocery retailer — BOPIS + SFS
│   ├── dom-rules/
│   ├── integration-design/
│   └── performance-tuning/
├── project-beta/           # Fashion retailer — marketplace + returns
│   ├── dom-rules/
│   ├── returns-flow/
│   └── extension-patterns/
└── templates/              # Industry standard patterns
    ├── omnichannel-dom-template.xml
    └── ship-from-store-config.xml
```

#### Step 3.8 — Add a separate MCP server for references

Add a second mcp-local-rag instance to your workspace.yaml with
its own `BASE_DIR`. This creates a completely separate vector
index — the two servers never mix results:

```yaml
mcp_servers:
  # YOUR project — ground truth
  - id: project-knowledge
    transport: stdio
    command: ["npx", "-y", "mcp-local-rag"]
    env:
      BASE_DIR: "${PROJECT_KNOWLEDGE_DIR}"

  # Reference designs — inspiration, NOT ground truth
  - id: reference-designs
    transport: stdio
    command: ["npx", "-y", "mcp-local-rag"]
    env:
      BASE_DIR: "${REFERENCE_DESIGNS_DIR}"
```

Set both env vars:

```bash
export PROJECT_KNOWLEDGE_DIR=~/sterling-knowledge
export REFERENCE_DESIGNS_DIR=~/sterling-references
```

Ingest the reference docs the same way as Step 3.4:

```bash
STERLING_KNOWLEDGE_DIR=~/sterling-references \
  uv run python scripts/ingest-docs.py
```

#### Step 3.9 — Create separate skills for each source

This is critical — separate skills let you control which agents
can access which knowledge source:

Create `skills/search-project.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: search-project
  name: Search Current Project
  description: >
    Searches the CURRENT project's Sterling configuration, extensions,
    and documentation. This is ground truth — results from this skill
    reflect what actually exists in the project.
category: capability
implementation:
  type: mcp_tool
  server: project-knowledge
  tool: query_documents
iam:
  required_scopes: [knowledge:read]
provenance:
  authored_by: human
  version: 1.0.0
```

Create `skills/search-reference-designs.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: search-reference-designs
  name: Search Reference Designs
  description: >
    Searches reference designs from OTHER Sterling implementations.
    Results are patterns and inspiration — NOT the current project's
    configuration. Always label citations from this source as
    reference material, never as current project fact.
category: capability
implementation:
  type: mcp_tool
  server: reference-designs
  tool: query_documents
iam:
  required_scopes: [knowledge:read]
provenance:
  authored_by: human
  version: 1.0.0
```

#### Step 3.10 — Update archetype prompts to prevent hallucination

This is the most important part. Add this block to both the
Sterling OMS Architect and Retail Domain Expert system prompts:

```
KNOWLEDGE SOURCES — CRITICAL DISTINCTION:

You have access to two separate knowledge bases. You MUST
distinguish between them in every response:

1. search-project (project-knowledge server):
   YOUR project's actual configuration, extensions, and docs.
   This is GROUND TRUTH. When answering "how does our system
   work?" or "what is our current configuration?", ONLY cite
   this source.

2. search-reference-designs (reference-designs server):
   Designs from OTHER Sterling implementations. This is
   INSPIRATION, not fact. When citing a reference design:
   - ALWAYS prefix with "In a reference implementation..." or
     "A pattern from another project..."
   - NEVER state a reference pattern as if it exists in the
     current project
   - NEVER say "our system does X" based on reference data

RULES:
1. Questions about current state → search project-knowledge ONLY
2. Questions about solutions → search BOTH, but label clearly:
   "Our current configuration does X (from project-knowledge).
    A reference project handled this by Y (from reference-designs).
    To adapt this, we would need to change Z."
3. If a reference design contradicts current configuration, flag
   it: "Note: the reference approach differs from our current
   setup which does X instead."
4. When recommending changes, always state what currently exists
   FIRST (from project-knowledge), then what the reference shows,
   then your recommendation.
```

Update the archetype skills lists:

```yaml
# Sterling OMS Architect — gets both sources
skills:
  - search-project           # ground truth
  - search-reference-designs  # inspiration
  - read-context
  - query-swarmkit-docs

# Config Validator — gets ONLY current project (no references)
skills:
  - search-project
  - read-context
```

Agents that should never hallucinate reference patterns as current
project state (config validators, deployment reviewers) get only
`search-project`. Agents that benefit from broader context
(solution architects, retail experts) get both.

#### Example: how the agent should respond

**Bad** (conflates sources):
> "Our DOM rules use cost-based sourcing with zone prioritization
> configured in the YFS_SOURCING_RULE table."
> (This was from a reference project, not the current one.)

**Good** (labeled sources):
> "Our current DOM configuration uses priority-based sourcing with
> 3 rules (from project config: dom-rules/sourcing-sequence.xml).
>
> A reference implementation for a similar retailer used cost-based
> sourcing with zone prioritization, which reduced shipping costs
> by ~15% (from reference: project-alpha/dom-rules/).
>
> To adapt this, we would need to add a cost-calculation condition
> to our existing sourcing sequence and configure zone-to-node
> mappings."

## Step 4 — Create skills

### Sterling documentation search

Create `skills/search-sterling-docs.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: search-sterling-docs
  name: Search Sterling Documentation
  description: >
    Semantic search over Sterling OMS product documentation and
    project-specific docs. Uses mcp-local-rag's query_documents
    tool with hybrid keyword + vector search for high-quality
    retrieval over large doc sets.
category: capability
implementation:
  type: mcp_tool
  server: sterling-knowledge
  tool: query_documents
iam:
  required_scopes: [knowledge:read]
provenance:
  authored_by: human
  version: 1.0.0
```

### Expand context around search results

Create `skills/read-context.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: read-context
  name: Read Context Around Search Result
  description: >
    Expands context around a search result — reads neighboring
    chunks from the same file. Used when a search hit returns a
    partial XML element or code snippet that needs surrounding
    context to understand.
category: capability
implementation:
  type: mcp_tool
  server: sterling-knowledge
  tool: read_chunk_neighbors
iam:
  required_scopes: [knowledge:read]
provenance:
  authored_by: human
  version: 1.0.0
```

### Ingest new documentation

Create `skills/ingest-doc.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: ingest-doc
  name: Ingest Documentation
  description: >
    Adds or updates a file in the knowledge base index. Use when
    a referenced document isn't indexed yet or has been updated.
category: capability
implementation:
  type: mcp_tool
  server: sterling-knowledge
  tool: ingest_file
iam:
  required_scopes: [knowledge:write]
provenance:
  authored_by: human
  version: 1.0.0
```

### Configuration review (decision skill)

Create `skills/config-review.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: config-review
  name: Sterling Configuration Review
  description: >
    Reviews a proposed Sterling OMS configuration change for
    correctness, performance impact, and alignment with best
    practices. Returns a structured verdict.
category: decision
outputs:
  type: object
  properties:
    verdict:
      type: string
      enum: [approve, needs-changes, reject]
    confidence:
      type: number
      minimum: 0
      maximum: 1
    reasoning:
      type: string
    risks:
      type: array
      items:
        type: object
        properties:
          area:
            type: string
          severity:
            type: string
            enum: [critical, high, medium, low]
          description:
            type: string
        required: [area, severity, description]
  required: [verdict, confidence, reasoning]
implementation:
  type: llm_prompt
  prompt: >
    Review the proposed Sterling OMS configuration change for:
    1. Correctness (valid XML, correct element names and attributes)
    2. Performance impact (will this cause agent bottlenecks, DB load?)
    3. Best practices (is this the recommended Sterling approach?)
    4. Integration impact (does this affect inbound/outbound flows?)
    5. Upgrade safety (will this survive a Sterling version upgrade?)

    Search the knowledge base for relevant documentation before
    producing your verdict.
provenance:
  authored_by: human
  version: 1.0.0
```

### Business requirements validation

Create `skills/business-validation.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: business-validation
  name: Business Requirements Validation
  description: >
    Validates a proposed technical change against retail business
    requirements, customer experience impact, and operational
    feasibility.
category: decision
outputs:
  type: object
  properties:
    verdict:
      type: string
      enum: [aligned, needs-adjustment, misaligned]
    confidence:
      type: number
      minimum: 0
      maximum: 1
    reasoning:
      type: string
    customer_impact:
      type: string
    operational_impact:
      type: string
  required: [verdict, confidence, reasoning]
implementation:
  type: llm_prompt
  prompt: >
    Evaluate the proposed change from a retail business perspective:
    1. Does it meet the stated business requirement?
    2. What is the customer experience impact?
    3. What is the operational impact (store ops, call center)?
    4. Are there industry best practices that apply?
    5. What are the edge cases or seasonal considerations?
provenance:
  authored_by: human
  version: 1.0.0
```

## Step 5 — Build a topology

### Sterling Solution Review topology

Create `topologies/solution-review.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: solution-review
  version: 0.1.0
agents:
  root:
    id: root
    role: root
    model:
      provider: anthropic
      name: claude-sonnet-4-6
    prompt:
      system: >
        You are the solution review coordinator. When given a
        Sterling OMS question, configuration change, or design
        proposal, delegate to the appropriate specialist:
        - sterling-architect for technical Sterling questions
        - retail-expert for business/domain questions
        Synthesise their responses into a final recommendation.
    children:
      - id: sterling-architect
        role: worker
        archetype: sterling-oms-architect
      - id: retail-expert
        role: worker
        archetype: retail-domain-expert
```

## Step 6 — Validate and run

```bash
# Validate everything resolves
swarmkit validate . --tree

# Ask a Sterling question
SWARMKIT_PROVIDER=openrouter SWARMKIT_MODEL=meta-llama/llama-3.3-70b-instruct \
  swarmkit run . solution-review \
  --input "We need to implement ship-from-store for 200 retail locations. \
           What DOM rules do we need, and what agent configuration changes \
           are required?" \
  --verbose
```

## Step 7 — Iterate based on results

After running the swarm, check the results:

```bash
# View what each agent did
swarmkit logs . --last 1

# Ask the LLM to explain the run
swarmkit why solution-review .

# Ask follow-up questions
swarmkit ask "Which agent took the longest and why?" -w .
```

If the responses are too generic (not referencing your project):
- **Check the knowledge base** — are the files being indexed?
  Test with `swarmkit knowledge-server` and call `list_sources`.
- **Refine the prompts** — add more project-specific context to
  the archetype system prompts (your org's naming conventions,
  specific Sterling version, known constraints).
- **Add more knowledge sources** — the more project-specific
  context the agents have, the better the output.

If you need help refining, use `swarmkit edit`:

```bash
swarmkit edit . --input "The sterling architect isn't referencing \
  our DOM rules. Make sure it searches project config before answering."
```

## What knowledge makes the biggest difference

In order of impact:

1. **Your project's Sterling configuration XMLs** — the agent
   knowing your actual DOM rules, agent configs, and pipeline
   definitions is the single biggest quality improvement.

2. **Your project's design documents** — architecture decisions,
   integration specs, custom extension documentation.

3. **Sterling product documentation** — the InfoCenter pages
   for your specific Sterling version. The LLM has general
   Sterling knowledge but may not know version-specific APIs.

4. **Extension source code** — your Java user exits and custom
   APIs. The agent can reference actual implementation when
   answering questions about custom behavior.

5. **Industry/company standards** — your organization's coding
   standards, naming conventions, deployment procedures.

## Expanding the swarm

Once the basic two-agent topology works, consider adding:

- **Sterling DBA** archetype — for database schema questions,
  query optimization, and data migration
- **Integration Specialist** archetype — for EDI, API, and
  message flow questions
- **Test Analyst** archetype — for test scenario generation
  based on configuration changes
- **Code Review Swarm** — use the reference code-review
  topology against your Sterling extension code

Each new archetype follows the same pattern: YAML + detailed
prompt + knowledge-backed skills.
