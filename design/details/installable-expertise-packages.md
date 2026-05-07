# Installable Expertise Packages — SwarmKit as MCP Server

**Status:** Design note — future milestone  
**Design ref:** §9 (three-component system), §18 (MCP integration)

## Problem

SwarmKit workspaces today are manually assembled — you create the
directory, write the YAML, configure MCP servers, ingest knowledge,
and set up env vars. This works for the workspace author but creates
a high barrier for anyone else who wants to use that expertise.

Meanwhile, AI assistants (Claude Desktop, Cursor, Claude Code, Copilot)
support MCP servers as extensible tool providers. Users can add new
capabilities to their AI assistant by installing MCP servers.

The gap: there's no way to package a SwarmKit workspace as something
another user can install and use from their AI assistant.

## Vision

SwarmKit workspaces become **installable expertise packages** that any
MCP client can use. The workspace author packages the knowledge,
topologies, skills, and tool servers. The user installs the package
and their AI assistant gains domain expertise.

```bash
# Install domain expertise packages
swarmkit install @delivstat/sterling-oms
swarmkit install @acme/compliance-reviewer
swarmkit install @company/sre-oncall

# Claude/Cursor automatically discovers installed workspaces
# and can delegate complex domain tasks to them
```

Inside Claude Desktop:
```
You: "Check if this order creation code follows Sterling best practices"

Claude: [calls swarmkit.run_topology with topology="code-review"]
        [SwarmKit runs 3-leader code review with CDT config, API javadocs,
         project code, and Sterling patterns]

Claude: "The code review found 3 issues..."
```

## Architecture

### SwarmKit MCP Server

A single MCP server that auto-discovers installed workspaces and
exposes their topologies as tools:

```
Claude Desktop / Cursor / Claude Code
    ↓ (MCP protocol)
SwarmKit MCP Server
    ↓ (discovers installed workspaces)
    ├── @delivstat/sterling-oms
    │   ├── run_sterling_assistant(input)
    │   ├── run_code_review(input)
    │   ├── search_sterling_docs(query)
    │   └── search_sterling_config(pattern)
    ├── @acme/compliance-reviewer
    │   ├── run_compliance_check(document)
    │   └── search_policies(query)
    └── @company/sre-oncall
        ├── run_incident_analysis(error)
        └── search_runbooks(query)
```

### Tools exposed per workspace

| Tool | Description |
|------|-------------|
| `run_{topology}` | Run a topology one-shot with input, return result |
| `search_{knowledge}` | Search the workspace's knowledge base |
| `list_topologies` | Show available topologies |
| `list_skills` | Show available skills and capabilities |
| `author_skill` | Create a new skill through authoring agent |

### Workspace package format

A workspace package is a tarball/zip containing:

```
package/
├── workspace.yaml          # topology, MCP servers, governance
├── topologies/             # agent graphs
├── archetypes/             # agent configurations
├── skills/                 # capability definitions
├── servers/                # custom MCP server scripts
├── knowledge/              # pre-built knowledge indexes (optional)
├── scripts/                # ingestion and setup scripts
├── package.yaml            # package metadata
└── README.md
```

`package.yaml`:

```yaml
name: "@delivstat/sterling-oms"
version: 1.0.0
description: >
  IBM Sterling OMS implementation assistant. Five topologies for
  solution review, QA, code review, coding, and general assistance.
  Includes CDT config server, ChromaDB RAG, API javadocs, and
  Jira/Confluence integration.
author: Srijith Kartha <srijith.kartha@delivstat.com>
license: MIT
requires:
  runtime: ">=1.0.30"
  providers:
    - openrouter     # or: any provider with tool-use support
  system:
    - poppler-utils  # for PDF rendering
  env:
    - OPENROUTER_API_KEY
    - STERLING_CDT_DIR     # optional: for CDT config
    - CONFLUENCE_URL       # optional: for Confluence access
topologies:
  - sterling-assistant     # exposed as run_sterling_assistant
  - code-review            # exposed as run_code_review
  - sterling-qa            # exposed as run_sterling_qa
knowledge:
  searchable: true         # expose search tools to MCP clients
```

### Registry

Packages are published to and installed from a registry:

```bash
# Publish
swarmkit publish .                     # publishes current workspace

# Install
swarmkit install @delivstat/sterling-oms
swarmkit install @acme/compliance-reviewer

# List installed
swarmkit packages

# Update
swarmkit install @delivstat/sterling-oms --upgrade
```

Registry options:
- **npm registry** — reuse existing infrastructure, familiar workflow
- **GitHub releases** — package per repo, tag-based versioning
- **SwarmKit registry** — dedicated service (more control, more work)

Recommendation: start with GitHub releases (tag + tarball), add npm
later for discoverability.

### MCP client configuration

```json
// Claude Desktop: ~/.claude/claude_desktop_config.json
{
  "mcpServers": {
    "swarmkit": {
      "command": "swarmkit",
      "args": ["mcp-serve"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-..."
      }
    }
  }
}
```

`swarmkit mcp-serve` starts an MCP server on stdio that auto-discovers
all installed workspace packages and exposes their topologies as tools.

## Use cases

### Enterprise knowledge assistant

An SRE installs the on-call workspace. From Claude Code during an
incident, they ask "has this error happened before?" Claude delegates
to SwarmKit which searches the incident knowledge base, checks
Grafana dashboards via MCP, reads runbooks, and correlates with past
incidents.

### Compliance review

A legal team installs the compliance workspace. From Claude Desktop,
they paste a contract clause and ask "does this comply with our
policies?" SwarmKit runs a compliance topology with policy documents,
regulatory knowledge base, and a Rynko Flow validation gate.

### Code review as a service

A team installs the code review workspace. GitHub Actions triggers
SwarmKit on every PR. The review topology (3 leaders, security +
quality + operations) produces a structured review report posted as
a PR comment.

### Domain-specific research

A pharma company installs a drug interaction workspace. Researchers
ask "what interactions exist between drug X and drug Y?" SwarmKit
searches PubMed, clinical trial databases, and internal research
notes, then synthesises a cited answer.

## What this enables

1. **Expertise as a product** — workspace authors package domain
   knowledge and sell access (or open-source it)
2. **Zero-config for users** — install and use, no workspace setup
3. **AI assistant augmentation** — Claude/Cursor/Copilot gain domain
   expertise they don't have natively
4. **Composability** — install multiple packages, each adds
   independent domain capabilities

## Implementation phases

### Phase 1: SwarmKit as MCP server (1-2 weeks)

- `swarmkit mcp-serve` command that exposes current workspace
  topologies as MCP tools
- Works with Claude Desktop, Cursor, Claude Code
- Single workspace, local only

### Phase 2: Package format + install (2-3 weeks)

- `package.yaml` spec
- `swarmkit publish` / `swarmkit install` via GitHub releases
- Multiple installed workspaces, auto-discovered

### Phase 3: Registry + discovery (4-6 weeks)

- Public registry for community packages
- Search, ratings, verified publishers
- Dependency resolution between packages

## Non-goals

- **Not a marketplace** — packages are free/open-source initially.
  Commercial packaging is a v2.0 concern.
- **Not a hosting service** — packages run locally on the user's
  machine. Cloud execution is a separate product.
- **Not a model provider** — the user brings their own API keys.
  The package provides the topology, knowledge, and skills.

## Open questions

1. How to handle large knowledge bases in packages? ChromaDB indexes
   can be hundreds of MB. Ship pre-built or ingest on install?
2. How to handle secrets/credentials that packages need (API keys,
   Confluence tokens)? The user must provide them, but the discovery
   UX needs to be smooth.
3. Should packages declare which models they're tested with, or be
   model-agnostic?
4. How to version knowledge separately from topology? The CDT config
   changes more often than the agent graph.
5. Can packages depend on other packages? (e.g. a Sterling compliance
   package depends on the base Sterling package)
