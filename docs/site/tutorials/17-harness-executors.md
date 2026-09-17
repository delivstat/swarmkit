# Level 17: Harness executors

Run a coding harness — Claude Code, Codex, Gemini CLI, opencode — as a **node** in a topology, under
the same governance, audit and gates as a model node.

## What you'll learn

- The `executor` seam: `kind: model` (the default) versus `kind: harness`
- Bundled adapters, `swarmkit adapters list/show/approve`, and why a launch block is approved by a person
- What a harness gets: a worktree, a task statement, its granted tools through the **governed MCP gateway**
- Mid-run questions: `on_unanswerable: deny | abort | relay`, trust accrual, `swarmkit review answer`
- `output_schema` and decision skills on a harness node
- The container sandbox tier

## The idea

A model node is a chat loop the runtime drives: prompt in, tool calls out, answer back. A harness is a
**session-holding, diff-producing subprocess** — it edits files, runs tests, asks questions. That is a
different way of *executing* a node, not a different kind of capability, so it lives behind the
`executor` abstraction ([design note](../design-notes/executor-abstraction.md)) rather than as a skill.

Nothing about the harness is Python in your workspace. The adapter is a declarative YAML artifact
([Executor adapter](../reference/executor-adapter.md)) that says how to launch the binary, how to read its
JSONL stream, and how to map its events onto the runtime's — the four big harnesses ship bundled.

## Build it

### 1. Pick an adapter

```bash
swarmkit adapters list ./workspace
```

```
  claude-code          bundled    pre-vetted
  codex                bundled    pre-vetted
  gemini-cli           bundled    pre-vetted
  opencode             bundled    pre-vetted
```

A *workspace* adapter (one you author under `adapters/`) shows `pending` until a person approves its
launch block — the command line that will be executed is the thing a reviewer inspects:

```bash
swarmkit adapters show my-harness ./workspace     # command + fingerprint
swarmkit adapters approve my-harness ./workspace  # a human action, recorded
```

### 2. Give an archetype the executor

```yaml
# archetypes/developer.yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: developer
  name: Developer
  description: Implements a change in the repository and returns the diff.
role: worker
defaults:
  model: { provider: anthropic, name: claude-opus-4-7 }   # used by the harness's own auth
  prompt:
    system: You implement exactly the change described, with tests.
executor:
  kind: harness
  ref: claude-code
  config:
    model: claude-opus-4-7
    allowed_tools: [Read, Edit, "Bash(git *)"]
provenance: { authored_by: human, version: 1.0.0 }
```

`executor` is optional; absent means `kind: model`. `config` is opaque to the runtime and validated by
the adapter.

### 3. Use it in a topology, gated

```yaml
# topologies/implement.yaml
agents:
  root:
    id: lead
    role: root
    model: { provider: anthropic, name: claude-opus-4-7 }
    prompt: { system: Plan the change, delegate the implementation, review the diff. }
    children:
      - id: developer
        role: worker
        archetype: developer
        skills: [repo-search]          # an mcp_tool skill — reaches the harness via the gateway
        funnel: code-review            # validate -> judge -> a human approves the diff
        output_schema:
          type: object
          required: [summary, files_changed]
          properties:
            summary: { type: string }
            files_changed: { type: array, items: { type: string } }
```

What the harness receives:

- a **git worktree** cut at `base_ref` (default `HEAD`), torn down on exit — the diff is carried out of
  the run as `result.diffs` and `GET /jobs/{id}/diff`, so the work survives the worktree;
- the **task statement**, with the `output_schema` rendered as an output contract it can read;
- its granted tools through an ephemeral, per-run **MCP gateway** (`mcp__swarmkit__<server>__<tool>`)
  — every call goes through the same permission seam and `requires:` rules as a model node's, and
  is audited as `skill.executed`. `agent` skills appear there too (Level 20).

### 4. Decide who answers its questions

A harness asks two kinds of things. A **permission** request ("may I run `rm -rf build`?") and an
**input** request ("PDF or markdown?"). The adapter's `on_unanswerable` says what happens outside the
launch grant:

| | behaviour |
|---|---|
| `abort` (default) | the run ends `needs_approval`; nothing was done that was not granted |
| `deny` | the request is refused in place and the harness continues |
| `relay` | the harness pauses, the request enters the review inbox, a person decides within `max_approval_wait_seconds`, the decision is fed back |

Under `relay`, approvals are scoped to the single action and **accrue trust**: after enough approvals
of the same (archetype, capability) pair, the runtime proposes an allowlist change, and a person
applies it:

```bash
swarmkit review list ./workspace --kind permission
swarmkit review approve <id> ./workspace
swarmkit review answer <id> "markdown" ./workspace   # an input request

swarmkit trust list ./workspace       # pending allowlist proposals (archetype <- capability, count)
swarmkit trust apply <id> ./workspace # adds it to executor.config.allowed_tools
```

### 5. Isolate it

Absent `sandbox`, the harness runs natively in the worktree. For a real boundary:

```yaml
# adapters/claude-code-sandboxed.yaml (spec excerpt)
sandbox:
  kind: container
  image: ghcr.io/example/harness:latest
  network: allowlist                 # deny (default) | allowlist
  allow: [api.anthropic.com]
  resources: { cpus: 2, memory: 4g, pids: 512 }
```

The gateway's host is added to the container's allowlist automatically; `stdio` MCP servers cannot be
reached from inside a container and are reported, not silently dropped.

## Run it

The bundled demos drive the **real** `claude-code` adapter against a scripted stream-json transcript,
so they need no key and no network:

```bash
just demo-harness-build             # developer harness -> diff -> code-review funnel -> human sign-off
just demo-harness-output-schema     # a diff corrected against a declared output schema
just demo-harness-decision-skills   # a decision skill judging a harness's output
just demo-harness-tool-outcomes     # what the harness actually called, per tool, in the audit
```

Read a run afterwards:

```bash
swarmkit trace <run-id> ./workspace          # the harness as one node: duration, tools, cost "unknown" unless reported
swarmkit logs ./workspace --run-id <run-id>  # exec.* events, tool calls, the gate
```

## What happened

- The harness ran as a node: same topology, same gate, same audit. `swarmkit trace` shows it beside
  the model nodes; its interior model calls are not visible unless the harness reports them, and the
  trace says so rather than guessing.
- Every tool call it made went through the gateway, so the audit has *which* tools, with arguments
  and outcomes — not just "43 advertised, 55 called".
- The diff left the worktree with the run; the funnel judged it; a person approved it.

## Learn more

- [Executor adapter artifact](../reference/executor-adapter.md) — every `spec` field
- [Executor abstraction](../design-notes/executor-abstraction.md) — why a harness is an executor, not a skill
- [Harness output schema](../design-notes/harness-output-schema.md) · [Harness decision skills](../design-notes/harness-decision-skills.md) · [Harness tool outcomes](../design-notes/harness-tool-outcomes.md)
- [Executor MCP gateway](../design-notes/executor-mcp-gateway.md) · [Container sandbox](../design-notes/executor-container-sandbox.md)
