# Skills

Skills are SwarmKit's only extension primitive. Every capability an agent can exercise is a skill.

## Categories

| Category | Purpose | Returns |
|----------|---------|---------|
| **capability** | Give agent a new ability (API call, search, generation) | Output data |
| **decision** | Let agent evaluate or judge (validation, classification) | Verdict + confidence + reasoning |
| **coordination** | Enable handoff between agents (A2A, escalation) | Task status |
| **persistence** | Enable recording (audit log, knowledge base write) | Write confirmation |

## Importing from the public MCP ecosystem

There are 7,000+ community MCP servers. Before writing a skill from scratch, check if a public server already does what you need. Wrapping a public MCP server is three config files:

**1. Add the server to workspace.yaml:**

```yaml
mcp_servers:
  - id: brave-search
    transport: stdio
    command: ["npx", "-y", "@anthropic/brave-search-mcp"]
    env:
      BRAVE_API_KEY: "${BRAVE_API_KEY}"
```

**2. Create a skill YAML that references it:**

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: search-web
  name: Web Search
  description: Search the web for current information using Brave Search.
category: capability
implementation:
  type: mcp_tool
  server: brave-search
  tool: brave_web_search
provenance:
  authored_by: human
  version: 1.0.0
```

**3. Add the skill to an archetype:**

```yaml
defaults:
  skills:
    - search-web
```

The runtime discovers the server's tools via MCP protocol at startup and forwards the tool schema to the LLM so it knows the correct parameter names.

### Common public MCP servers

| Server | npm package | Use case |
|--------|------------|----------|
| GitHub | `@modelcontextprotocol/server-github` | Repos, PRs, issues, actions |
| Filesystem | `@modelcontextprotocol/server-filesystem` | Read/write local files |
| Brave Search | `@anthropic/brave-search-mcp` | Web search |
| Slack | `@anthropic/slack-mcp` | Channels, messages |
| PostgreSQL | `@modelcontextprotocol/server-postgres` | Database queries |
| Google Drive | `@anthropic/gdrive-mcp` | Docs, sheets |
| Qdrant | `mcp-server-qdrant` | Vector store + RAG |

### Custom MCP servers

For capabilities without a public server, write a custom one. SwarmKit can scaffold these:

```bash
swarmkit author mcp-server .
```

This generates a Python MCP server using the `mcp` SDK, a skill YAML, and a workspace.yaml entry through conversation.

## Reference skills

20 reference skills ship with SwarmKit in the `reference/skills/` directory.

### Capability skills

| Skill | MCP server | Tool |
|---|---|---|
| github-repo-read | github | get_file_contents |
| github-pr-read | github | get_pull_request |
| github-issue-read | github | get_issue |
| query-swarmkit-docs | swarmkit-knowledge | search_docs |
| list-reference-skills | swarmkit-knowledge | list_reference_skills |
| get-schema | swarmkit-knowledge | get_schema |
| validate-workspace | swarmkit-knowledge | validate_workspace |
| read-workspace-file | swarmkit-knowledge | read_workspace_file |
| write-workspace-file | swarmkit-knowledge | write_workspace_file |
| run-tests | swarmkit-knowledge | run_pytest |
| search-codebase | (template) | — |
| summarize-review | (llm_prompt) | — |

### Decision skills

| Skill | Outputs |
|---|---|
| code-quality-review | verdict, confidence, reasoning, issues |
| security-scan | verdict, confidence, reasoning, findings |
| test-coverage-review | verdict, confidence, reasoning, gaps |
| qa-verdict | verdict, confidence, reasoning |
| deploy-risk-review | verdict, confidence, reasoning, risks |
| lint-check | verdict, confidence, reasoning, violations |

Decision skills require an `outputs` block with JSON Schema defining the verdict structure. The runtime enforces this: if the model's output doesn't match the schema, it gets field-specific error messages and retries automatically.

### Coordination skills

| Skill | Description |
|---|---|
| peer-handoff | A2A context packaging for leader-to-leader handoff |

### Persistence skills

| Skill | Description |
|---|---|
| audit-log-write | Structured event to governance audit log |

## Sterling OMS workspace skills (21 skills)

The Sterling workspace demonstrates real-world skill design:

| Skill | Type | Server | Purpose |
|---|---|---|---|
| search-sterling-docs | capability | sterling-product-docs | ChromaDB semantic search over 17K product docs |
| search-docs-exact | capability | sterling-product-fts | FTS5 exact keyword search |
| search-project-docs | capability | sterling-project-docs | ChromaDB search over project docs |
| search-reference-designs | capability | reference-designs | Search sanitised designs from other projects |
| get-service-config | capability | sterling-config | Parsed CDT service configuration |
| get-pipeline | capability | sterling-config | Pipeline steps, conditions, pickup transactions |
| search-configs | capability | sterling-config | Grep across all CDT config tables |
| grep-project-code | capability | code-graph | Grep file contents (returns relative paths) |
| read-file-lines | capability | code-graph | Read specific line range from source files |
| verify-code-citations | capability | code-graph | Check file:line citations against actual source |
| get-api-input-xml | capability | sterling-api-javadocs | API input XML structure from javadocs |
| get-api-output-xml | capability | sterling-api-javadocs | API output XML structure |
| read-project-code | capability | project-code | Read full source file |
| write-notes | capability | notes-dir | Write analysis to notes directory |

## Skill anatomy

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: code-quality-review         # lowercase-kebab, unique in workspace
  name: Code Quality Review
  description: >
    Evaluates code changes against quality standards. Returns pass/fail
    verdict with specific issues found.
category: decision
outputs:                           # required for decision skills
  type: object
  properties:
    verdict:
      type: string
      enum: [pass, fail, needs-changes]
    confidence:
      type: number
      minimum: 0
      maximum: 1
    reasoning:
      type: string
    issues:
      type: array
      items:
        type: object
        properties:
          file: { type: string }
          line: { type: integer }
          severity: { type: string, enum: [error, warning, info] }
          message: { type: string }
  required: [verdict, confidence, reasoning]
implementation:
  type: mcp_tool                   # or: llm_prompt
  server: review-server
  tool: check_quality
iam:
  required_scopes: [repo:read]
provenance:
  authored_by: human               # human | authored_by_swarm | vendor_published
  version: 1.0.0
```

## Provenance

Every skill declares who authored it. This affects runtime trust defaults:

| Value | Meaning | Trust |
|-------|---------|-------|
| `human` | Hand-authored by user | Full trust |
| `authored_by_swarm` | Produced by authoring swarm, human-approved | Locked until first human review |
| `derived_from_template` | Generated from template | Partial oversight |
| `imported_from_registry` | Community registry | Depends on registry vetting |
| `vendor_published` | Commercial vendor | Depends on vendor relationship |
