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
  type: mcp_tool                   # or: llm_prompt | composed | command | agent
  server: review-server
  tool: check_quality
iam:
  required_scopes: [repo:read]
provenance:
  authored_by: human               # human | authored_by_swarm | vendor_published
  version: 1.0.0
```

## Another agent as a skill (`implementation.type: agent`)

One skill type, two resolutions — a topology in this workspace, or a remote agent reached through
its A2A Agent Card (`design/details/a2a-interop.md`). Either way it is a skill: the same
permission seam as an MCP tool or a command, the same `requires:` prerequisites, the same audit of
the call.

```yaml
implementation:
  type: agent
  # exactly one of:
  topology: deep-research                                          # same workspace → child run, no wire
  card_url: https://research.internal/.well-known/agent-card.json  # elsewhere → A2A task API
  skill_id: deep-research          # remote only: which card skill; omitted = the card's first
  credentials_ref: research-agent  # remote only: a workspace `credentials` entry, sent as a bearer
  timeout_s: 600
  on_unanswerable: agent           # agent | relay | abort — the harness adapter's words
  max_agent_answers: 2
  permission: cautious             # open | cautious | strict | readonly
  effects: read                    # read | write | unknown; readonly allows only `read`
```

The tool the model sees takes `{input, context?}` and returns the other agent's answer as text.

**Local (`topology:`).** The target runs in-process as a child of the caller's run: its own run id
and trace, a job row with `parent_job_id` and `source: agent`, the parent's MCP servers shared (and
never closed by the child), the same correlation. A missing target fails the workspace load, not
the first run. Depth is capped at 3: a topology calling a topology that calls it back is a cycle.

**Remote (`card_url:`).** The card is fetched on first use and cached; `skill_id` must be on it.
The call is `message/send` with our run id as the A2A `contextId` (so two instances' records join
on it), then `tasks/get` until the task ends or asks for input. Past `timeout_s` the remote task
is cancelled and the call fails.

**When the other agent asks a question** (`input-required`), `on_unanswerable` decides, with the
words a harness adapter uses:

| policy | behaviour |
|---|---|
| `agent` (default) | The question comes back as the tool result — `{"status": "input_required", "task_id", "question"}` — and the calling agent answers by calling the skill again with `{task_id, answer}`. Audited as `executor.input_response` with `responder: agent:<id>`. After `max_agent_answers` on one task, the next question is relayed. |
| `relay` | A person answers through the review queue — the same `input_request` item and bounded wait a harness question uses; no answer in time fails the call and cancels the remote task. |
| `abort` | The call fails with the question as the reason; the remote task is cancelled. |

A **human gate** on the other side (a SwarmKit run parked on approval) is never the agent's to
answer, whatever the policy: the result says `kind: human_gate` and names the gate; a person
resolves it there. No funnel is configured on the skill — a child topology runs its own funnels,
a remote SwarmKit runs its own, and the caller's funnel gates what the caller does with the result.

**From a harness node.** An `agent` skill granted to a harness (Claude Code, opencode, …) is offered
through the governed MCP gateway as the flat tool `agent__<skill>` — the same executor, tier and
audit as from a model node; the child run is attributed to the harness's run.

**`pack:workspace`.** Every topology in the workspace is also synthesized as an `agent` skill named
`topology-<name>` (`cautious`, `effects: unknown`), the way command packs synthesize theirs, so a
supervisor that may run any topology here says so in one line:

```yaml
agents:
  root:
    skills: [pack:workspace]     # topology-<name> for every topology, now and later
```

Nothing is granted by default — the card lists every topology to the outside, but inside the
workspace an agent reaches only what its topology names. Unlike `pack:<command-pack>`, the grant is
not filtered to reads (running a topology is never `read`); every call still goes through the tier
and the audit. Naming a hand-authored skill `topology-<x>` is a collision error, and `workspace` is
reserved as a command-pack id.

## Constraints

`constraints` is optional on any skill: `max_latency_ms`, `timeout_seconds`, `retry: {attempts, backoff: exponential | linear | none}`, and `on_failure: escalate_to_human | fail | retry | fallback` — what the runtime does when the skill's call fails after its retries. `escalate_to_human` files a review item rather than returning an error to the agent; `fallback` hands the failure to the agent as a tool error it can act on.

## Provenance

Every skill declares who authored it. This affects runtime trust defaults:

| Value | Meaning | Trust |
|-------|---------|-------|
| `human` | Hand-authored by user | Full trust |
| `authored_by_swarm` | Produced by authoring swarm, human-approved | Locked until first human review |
| `derived_from_template` | Generated from template | Partial oversight |
| `imported_from_registry` | Community registry | Depends on registry vetting |
| `vendor_published` | Commercial vendor | Depends on vendor relationship |
