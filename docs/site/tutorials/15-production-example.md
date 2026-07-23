# Level 15: Production Example

A complete workspace that combines every feature from Levels 1-14 into a production-ready content review platform.

!!! tip "See a real delivery pipeline, end to end"
    The workspace below is a teaching example. For a **video walkthrough of a real delivery pipeline** — first-class [Funnels](../reference/funnel.md), a [StageGraph](../reference/stage-graph.md) sequenced by a durable saga controller, and integration [Contracts](../reference/contract.md), each shown running in the composer — see the **[SDLC pipeline walkthrough →](../sdlc-example/)** (source: `examples/sdlc-pipeline`).

## What you'll build

A **Technical Documentation Review** workspace that:
- Accepts documentation PRs via webhook
- Runs a 6-agent review swarm (accuracy, clarity, completeness, code examples, security, consistency)
- Uses ChromaDB for existing documentation search
- Checks for drift from the project's writing style
- Enforces output schema with structured review format
- Saves review memories for pattern detection
- Canary-deploys new review criteria
- Produces a final review posted as a PR comment

## Architecture

```
Webhook (GitHub PR) → SwarmKit Serve
    ↓
  Root Coordinator
    ├── Research Leader
    │   ├── Doc Searcher (ChromaDB knowledge)
    │   └── Code Validator (runs examples)
    └── Review Leader
        ├── Accuracy Checker (fact verification)
        ├── Clarity Reviewer (readability)
        ├── Security Scanner (credential leaks)
        └── Consistency Checker (style guide)
    ↓
  Synthesis → Structured Review JSON
    ↓
  Post to GitHub PR as comment
```

## The complete workspace

### workspace.yaml

```yaml
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: doc-review
  name: Documentation Review Platform
  description: >
    Multi-agent documentation review. Webhook-triggered, knowledge-grounded,
    governance-enforced, canary-deployed.

governance:
  provider: mock
  decision_skills:
    - id: content-filter
      trigger: pre_input
      scope: "*"
    - id: quality-check
      trigger: post_output
      scope: "review-*"
    - id: grounding-check
      trigger: post_output
      scope: "doc-searcher"
    - id: memory-reader
      trigger: pre_input
      scope: "coordinator"
    - id: memory-writer
      trigger: post_output
      scope: "coordinator"
  limits:
    max_steps_per_agent: 25
    max_steps_per_run: 150
    max_cost_per_run_usd: 2.00

credentials:
  github-token:
    source: env
    config:
      env: GITHUB_TOKEN

mcp_servers:
  - id: github
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
    permission: cautious

  - id: knowledge
    transport: stdio
    command: ["uv", "run", "servers/search_server.py"]
    env:
      CHROMADB_PATH: "./knowledge/chromadb"
    permission: readonly

  - id: filesystem
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."]
    permission: readonly

server:
  host: "0.0.0.0"
  port: 8000
  auth:
    provider: api_key
  jobs:
    max_concurrent: 3
    timeout_seconds: 600
  mcp:
    enabled: true
  canary:
    routes:
      - topology: doc-review
        versions:
          - version: "1.0.0"
            weight: 90
          - version: "1.1.0"
            weight: 10
            promote_when:
              min_runs: 30
              error_rate_below: 0.05
              drift_below: 0.40

storage:
  runtime:
    backend: sqlite
```

### Topology

```yaml
# topologies/doc-review.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  id: doc-review
  name: Documentation Review
  version: "1.0.0"
  description: >
    6-agent review of documentation changes. Structured delegation
    with research and review phases.
runtime:
  planning:
    scope_required: true
    two_phase: true
  synthesis:
    provider: openrouter
    model: deepseek/deepseek-v4-pro
    prompt: |
      Synthesize the review findings into a GitHub PR comment.
      Use this format:
      ## Documentation Review
      ### Critical Issues (must fix)
      ### Suggestions (should fix)
      ### Style Notes (nice to have)
      ### Summary
      Include specific line references where possible.
agents:
  root:
    id: coordinator
    role: root
    archetype: review-coordinator
    intent_monitoring:
      enabled: true
      threshold: 0.75
      on_drift: nudge
    children:
      - id: research-lead
        role: leader
        archetype: research-leader
        children:
          - id: doc-searcher
            role: worker
            archetype: doc-searcher
            skills:
              - search-knowledge
              - read-file
          - id: code-validator
            role: worker
            archetype: code-validator
            skills:
              - read-file
      - id: review-lead
        role: leader
        archetype: review-leader
        children:
          - id: review-accuracy
            role: worker
            archetype: accuracy-checker
          - id: review-clarity
            role: worker
            archetype: clarity-reviewer
          - id: review-security
            role: worker
            archetype: security-scanner
            skills:
              - read-file
          - id: review-consistency
            role: worker
            archetype: consistency-checker
            depends_on: [doc-searcher]
```

### Trigger

```yaml
# triggers/pr-review.yaml
apiVersion: swarmkit/v1
kind: Trigger
metadata:
  id: pr-review
  name: PR Documentation Review
type: webhook
topology: doc-review
enabled: true
auth:
  method: hmac
  secret: "${WEBHOOK_SECRET}"
```

### Run it

```bash
# Start the server
export SWARMKIT_API_KEY=my-api-key
export GITHUB_TOKEN=ghp_...
export WEBHOOK_SECRET=my-webhook-secret
swarmkit serve .

# Manual test
curl -X POST http://localhost:8000/run/doc-review \
  -H "Authorization: Bearer my-api-key" \
  -H "Content-Type: application/json" \
  -d '{"input": "Review PR #42 on myorg/docs-repo"}'

# Check status
curl -H "Authorization: Bearer my-api-key" http://localhost:8000/canary

# View trace
swarmkit trace -w .
```

## What's happening

1. **Webhook arrives** → trigger fires `doc-review` topology
2. **Canary router** → routes 90% to v1.0.0, 10% to v1.1.0
3. **Memory-reader** → injects prior review context ("this repo often has X issues")
4. **Content-filter** → blocks malicious PR descriptions (pre_input gate)
5. **Coordinator** → creates scope + task plan
6. **Research phase** → doc-searcher finds relevant existing docs, code-validator checks examples
7. **Review phase** → 4 workers analyze accuracy, clarity, security, consistency in parallel
8. **Quality-check** → validates each worker's output (post_output gate)
9. **Grounding-check** → ensures doc-searcher's findings are real (post_output gate)
10. **Drift detection** → catches if any agent wanders from the review task
11. **Synthesis** → V4 Pro combines findings into structured PR comment
12. **Memory-writer** → saves review insights for future context
13. **Circuit breaker** → enforced $2 max cost, 150 max steps
14. **Audit trail** → every step logged to SQLite with redaction

This single workspace exercises the Level 1–14 features across a real workflow. For the pipeline-orchestration features that shipped later — first-class **Funnel** gates, **StageGraph** pipelines sequenced by a saga controller, integration **Contracts** with checked locks, and multi-party approval — see [Level 16](16-pipelines.md) and the [SDLC pipeline walkthrough](../sdlc-example/).

## Features checklist

- [x] Topology as data (YAML)
- [x] Agent hierarchy (root → leaders → workers)
- [x] Parallel execution (4 reviewers)
- [x] DAG dependencies (consistency depends on doc-searcher)
- [x] Structured delegation (task plans, scopes)
- [x] Dual model (tool_model + synthesis model)
- [x] Output schema (structured review JSON)
- [x] Archetypes (8 specialist configs)
- [x] Skills (capability + decision)
- [x] MCP servers (GitHub, ChromaDB, filesystem)
- [x] Permission tiers (readonly, cautious)
- [x] Decision gates (content-filter, quality-check, grounding-check)
- [x] Circuit breakers (cost, steps)
- [x] Memory (reader + writer)
- [x] Intent drift detection
- [x] Serve mode with auth
- [x] Webhook trigger
- [x] Canary deployment
- [x] Audit trail
- [x] Synthesis configuration

## What's next

You've completed the SwarmKit guided tutorial. You now know how to:
- Build agents from simple to complex
- Wire real tools via MCP
- Add governance and safety guardrails
- Debug with tracing and observability
- Deploy as an HTTP service
- Package and distribute your workspace

For more:
- [CLI reference](../reference/cli.md)
- [Serve mode reference](../reference/serve.md)
- [Design document](../architecture/design-overview.md)
- [Implementation plan](../architecture/implementation-plan.md)
