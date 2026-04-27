---
title: Code Review Swarm — reference topology for multi-leader coordination
description: Three-leader swarm (Engineering, QA, Operations) that reviews pull requests end-to-end. Demonstrates A2A handoffs, LLM judges, HITL gates, and MCP integration.
tags: [topology, reference, code-review, m6]
status: proposed
---

# Code Review Swarm

## Goal

The canonical reference topology — the one users see first when they ask
"what does a real Swael swarm look like?" Three leaders coordinate
a PR review from code analysis through QA to deployment approval. Every
pattern from the design doc is exercised: hierarchical delegation,
A2A leader coordination, LLM judges, HITL gates, MCP tool calls, and
governance-gated scopes.

## Non-goals

- **Production GitHub app.** The topology works against the GitHub MCP
  server and a fixture PR. A real GitHub App integration (webhooks,
  check-run status updates) is M9+ work.
- **Language-specific analysis.** The review is LLM-driven, not
  AST-based. Language-specific linters are future MCP skills.
- **Multi-repo coordination.** One repo, one PR, one review.

## Agent tree

```
root (supervisor-leader)
├── engineering-leader
│   ├── code-reader        (github-reader archetype)
│   ├── code-reviewer      (code-analyst archetype)
│   └── security-reviewer  (security-reviewer archetype)
├── qa-leader
│   ├── test-analyst       (code-analyst archetype, test focus)
│   └── qa-judge           (llm-judge archetype)
└── ops-leader
    └── deploy-reviewer    (llm-judge archetype, deploy focus)
```

**Root supervisor** — receives the PR reference, delegates to
engineering-leader first. After engineering completes, delegates to
qa-leader. After QA completes, delegates to ops-leader. Synthesises
the final review verdict from all three leaders' outputs.

**Engineering leader** — coordinates code analysis. Delegates to
code-reader (fetch PR diff + file contents via GitHub MCP), then to
code-reviewer (evaluate quality, patterns, maintainability) and
security-reviewer (evaluate security concerns) in parallel-ish
fashion (both get the same diff context). Synthesises engineering
verdict with confidence score.

**QA leader** — evaluates test coverage and quality implications.
test-analyst reviews the diff for test gaps, qa-judge produces a
structured verdict (pass/fail/needs-tests with confidence).

**Operations leader** — evaluates deployment readiness. deploy-reviewer
assesses risk (breaking changes, migration needs, config changes).
When confidence is below threshold → item lands in the review queue
for human approval (HITL gate). This is the mandatory human checkpoint
from design §4.2.

## Skill map

| Agent | Skills | Category |
|---|---|---|
| code-reader | github-repo-read, github-pr-read | capability |
| code-reviewer | code-quality-review | decision |
| security-reviewer | security-scan | decision |
| test-analyst | test-coverage-review | decision |
| qa-judge | qa-verdict | decision |
| deploy-reviewer | deploy-risk-review | decision |

**Existing skills** (from reference/skills/): github-repo-read,
github-pr-read.

**New skills** needed for this topology:
- `code-quality-review` — decision skill, LLM judge, evaluates code
  quality with verdict + confidence + reasoning
- `security-scan` — decision skill, evaluates security concerns
- `test-coverage-review` — decision skill, evaluates test gaps
- `qa-verdict` — decision skill, synthesises QA assessment
- `deploy-risk-review` — decision skill, evaluates deployment risk.
  Produces a structured verdict; low confidence triggers HITL.

All new skills are `llm_prompt` type (LLM-driven evaluation, not
MCP tool calls). This is intentional — the Code Review Swarm
demonstrates that decision skills powered by LLMs work alongside
capability skills powered by MCP servers.

## Archetypes

**Existing** (from §13.1 catalogue):
- `supervisor-leader` — root supervisor pattern
- `code-analyst-worker` — code analysis + review (renamed to
  `code-analyst` for clarity)
- `security-reviewer-worker` → `security-reviewer`
- `llm-judge-worker` → `llm-judge`

**New archetypes** for this topology:

| Archetype | Role | Skills | Model |
|---|---|---|---|
| `supervisor-leader` | root | (none — delegates only) | claude-sonnet-4-6 |
| `engineering-leader` | leader | (delegates to workers) | claude-sonnet-4-6 |
| `qa-leader` | leader | (delegates to workers) | claude-sonnet-4-6 |
| `ops-leader` | leader | (delegates to workers) | claude-sonnet-4-6 |
| `github-reader` | worker | github-repo-read, github-pr-read | claude-sonnet-4-6 |
| `code-analyst` | worker | code-quality-review | claude-sonnet-4-6 |
| `security-reviewer` | worker | security-scan | claude-sonnet-4-6 |
| `test-analyst` | worker | test-coverage-review | claude-sonnet-4-6 |
| `llm-judge` | worker | qa-verdict, deploy-risk-review | claude-sonnet-4-6 |

## HITL gates

Per design §4.2: "Operations Leader handles deployment with mandatory
human approval."

The deploy-reviewer's `deploy-risk-review` skill returns a structured
verdict. When `confidence < 0.8` or `verdict == "needs-review"`, the
result lands in the review queue (`FileReviewQueue`). The topology
output includes the review-queue item ID so the user can:

```bash
swael review list <workspace>
swael review show <item-id> <workspace>
swael review approve <item-id> <workspace>
```

## Leader coordination model

The root supervisor mediates all leader-to-leader communication
(design §5.3 hierarchical pattern). Leaders don't talk directly —
the root delegates sequentially:

1. Root → engineering-leader: "Review this PR"
2. Engineering-leader returns: verdict + analysis
3. Root → qa-leader: "Assess test coverage given this engineering review"
4. QA-leader returns: verdict + gaps
5. Root → ops-leader: "Evaluate deployment risk given engineering + QA verdicts"
6. Ops-leader returns: verdict (may trigger HITL)
7. Root synthesises final review

This is sequential, not parallel, because each leader's output
informs the next leader's evaluation. A parallel variant (all three
leaders review independently, root merges) is a valid alternative
topology — the sequential version is chosen for the reference because
it demonstrates the delegation chain more clearly.

## MCP server requirements

The topology needs the GitHub MCP server configured in workspace.yaml:

```yaml
mcp_servers:
  - id: github
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
    credentials_ref: github-pat
```

## Workspace layout

```
reference/
├── topologies/code-review.yaml
├── archetypes/
│   ├── supervisor-leader.yaml
│   ├── engineering-leader.yaml
│   ├── qa-leader.yaml
│   ├── ops-leader.yaml
│   ├── github-reader.yaml
│   ├── code-analyst.yaml
│   ├── security-reviewer.yaml
│   ├── test-analyst.yaml
│   └── llm-judge.yaml
└── skills/
    ├── github-repo-read.yaml     (existing)
    ├── github-pr-read.yaml       (existing)
    ├── github-issue-read.yaml    (existing)
    ├── code-quality-review.yaml
    ├── security-scan.yaml
    ├── test-coverage-review.yaml
    ├── qa-verdict.yaml
    └── deploy-risk-review.yaml
```

## Test plan

- **Schema validation:** every new YAML file passes JSON Schema validation.
- **Workspace resolution:** the reference workspace resolves cleanly
  with all archetype + skill refs expanded.
- **Topology compilation:** compiles into a LangGraph graph with mock
  providers (no real API calls).
- **Golden-path test:** with mock provider, the topology produces a
  structured review output through all three leaders.
- **Live pipeline test:** with a real provider + GitHub MCP server,
  review a real PR on delivstat/swael and verify the output includes
  engineering, QA, and ops verdicts.

## Implementation plan

### PR 1: Design note (this document)

Review before implementation.

### PR 2: Archetypes + skills + topology + tests

All reference artifacts + fixture workspace + resolution/compilation
tests. No live execution — mock providers only.

### PR 3: Live demo + just demo-code-review

Live execution against a real PR. Demo target in justfile.
