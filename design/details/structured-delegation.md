---
title: Structured Delegation Model (v2 Compiler)
status: proposed
design_ref: §14.3 (compiler), §6 (skills)
dependencies: [M8 (MCP skills), v1.1.15 (partial child results), v1.1.17 (delegation counts)]
---

# Structured Delegation Model

## Problem

The current delegation model is text-based and fragile:

1. **Crash = total loss.** A run with 5 agents doing 200+ tool calls crashes at the last step (e.g. invalid model ID on document-writer). All research from jira-researcher (50 turns), config-analyst (25 turns), docs-researcher (18 turns), and sterling-developer (50 turns) is gone. LangGraph checkpoints exist but only at node boundaries — mid-node work is lost entirely.

2. **Re-delegation loops.** Weaker models (DeepSeek V4 Flash, hy3-preview) ignore the "do NOT re-delegate" instruction and call the same child 4+ times with slightly different queries. The delegation count cap (v1.1.17) is a blunt instrument — it blocks legitimate follow-up questions.

3. **Context overflow.** The coordinator receives full 50KB+ child results in its context window. With 5 children, that's 250KB+ of text the model must parse. Smaller models can't handle this; larger models waste tokens on irrelevant detail.

4. **Invisible progress.** During a run, there's no way to see what children found. The user watches progress output but can't inspect findings until the run completes (or crashes).

5. **No incremental resume.** `--resume` restarts the last incomplete node from scratch. If jira-researcher was on turn 45 of 50 when the machine crashed, all 45 turns are lost.

## Design

### Core concept: planner-driven task execution

Replace ad-hoc `delegate_to_*` calls with a structured plan-execute-review loop. The coordinator (any agent with 2+ children) creates an explicit task plan, the compiler executes it, and the coordinator reviews results at checkpoints.

### Execution model

```
User input
    ↓
Coordinator: create-task-plan (one LLM call)
    → produces ordered task list with dependencies
    ↓
Compiler: execute task batch (independent tasks run in parallel)
    → each task: child agent runs, result persisted to disk
    → compiler updates tasks.json with status + key_findings
    ↓
Coordinator: review checkpoint (one LLM call)
    → reads task summaries (not full 50KB results)
    → calls update-task-plan to:
        - add new tasks discovered from results
        - remove pending tasks that are no longer needed
        - modify instructions for pending tasks with new context
        - assign self-tasks for synthesis/document structure
    ↓
Compiler: execute next task batch
    ↓
... repeat until no pending tasks ...
    ↓
Coordinator: final synthesis (one LLM call)
    → reads all key_findings + full results if needed
    → produces final output
```

### Tool injection rules

The compiler injects planning tools automatically based on child count:

| Children | Tools injected | Rationale |
|----------|---------------|-----------|
| 0 | None (worker) | Workers execute, they don't plan |
| 1 | `delegate_to_<child>` (v1 behavior) | No planning overhead for pass-through |
| 2+ | `create-task-plan` + `update-task-plan` + `read-task-result` | Orchestration benefits from structured planning |

No skill or archetype changes needed. The tools are compiler-injected, same as `delegate_to_*` today.

The coordinator needs to know which agents are available. The compiler injects this into the `create-task-plan` tool description dynamically:

```
Available agents for task assignment:
  - jira-researcher: Focused researcher for Jira issues and Confluence pages
  - config-analyst: Analyses Sterling CDT configuration
  - docs-researcher: Searches Sterling product docs, project docs, API references
  - sterling-developer: Writes Sterling extension code
  - document-writer: Creates structured documents following a sample format
  - self: You (the coordinator). Use for synthesis, diagram creation, or any task
    requiring your own skills (write-notes, create-diagram, etc.)
```

This list is generated from the agent's `children` + their archetype descriptions. The coordinator never needs to guess agent names.

The coordinator's system prompt is also auto-generated:

```
YOUR WORKFLOW:
1. Call create-task-plan to define tasks for your workers and yourself
2. The compiler will execute tasks and report back with summaries
3. Review results and call update-task-plan to add, remove, or modify tasks
4. When all tasks are complete, synthesize the final answer

Assign tasks to any available agent or to "self" for work you need to do
yourself (synthesis, document structure, diagrams).
```

### Task plan schema

```json
{
  "tasks": [
    {
      "id": "jira-research",
      "agent": "jira-researcher",
      "instruction": "Search for return order types, store and DC returns in Jira and Confluence",
      "depends_on": []
    },
    {
      "id": "config-lookup",
      "agent": "config-analyst",
      "instruction": "Get all return-related pipelines, services, and transactions",
      "depends_on": []
    },
    {
      "id": "docs-search",
      "agent": "docs-researcher",
      "instruction": "Find return order API docs and project docs",
      "depends_on": ["jira-research"]
    },
    {
      "id": "code-review",
      "agent": "sterling-developer",
      "instruction": "Check return order implementation code",
      "depends_on": ["config-lookup", "docs-search"]
    },
    {
      "id": "synthesize",
      "agent": "self",
      "instruction": "Create document structure with pipeline diagrams from all findings",
      "depends_on": ["code-review"]
    },
    {
      "id": "write-doc",
      "agent": "document-writer",
      "instruction": "Write DOCX following the structure from synthesis",
      "depends_on": ["synthesize"]
    }
  ]
}
```

Key properties:
- **`agent: "self"`** — coordinator executes this task itself using its own skills (write-notes, create-diagram, etc.)
- **`agent: "<child-id>"`** — must match an available child agent. The compiler validates this and rejects unknown agent IDs.
- **`depends_on`** — tasks without dependencies run in parallel. The compiler handles ordering.
- **IDs are user-readable** — the coordinator names them, not the compiler. This helps with the review checkpoint.

**Note:** The "Available agents" list in the tool description is dynamically generated by the compiler from the agent's `children` array + their archetype descriptions. The example above shows Sterling's agents — a different topology would show its own children.

### `create-task-plan` tool

```json
{
  "name": "create-task-plan",
  "description": "Create an execution plan for your workers and yourself. Each task is assigned to an available agent or to 'self' for tasks you handle. Tasks with no depends_on run in parallel. The compiler executes the plan and reports results at each checkpoint.\n\nAvailable agents:\n- jira-researcher: Focused researcher for Jira issues and Confluence pages\n- config-analyst: Analyses Sterling CDT configuration\n- docs-researcher: Searches Sterling product docs, project docs, API references\n- sterling-developer: Writes Sterling extension code\n- document-writer: Creates structured documents following a sample format\n- self: You (coordinator). Use for synthesis, diagrams, or tasks needing your own skills.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "tasks": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "id": {"type": "string", "description": "Unique task identifier (e.g. 'jira-research')"},
            "agent": {"type": "string", "description": "Child agent ID or 'self' for coordinator tasks"},
            "instruction": {"type": "string", "description": "What the agent should do"},
            "depends_on": {"type": "array", "items": {"type": "string"}, "description": "Task IDs that must complete first"}
          },
          "required": ["id", "agent", "instruction"]
        }
      }
    },
    "required": ["tasks"]
  }
}
```

### `update-task-plan` tool

Called at checkpoints when the coordinator reviews results:

```json
{
  "name": "update-task-plan",
  "description": "Modify the execution plan after reviewing results. Add new tasks, remove pending tasks, or update instructions for pending tasks. Cannot modify completed or in-progress tasks.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "add": {
        "type": "array",
        "items": {"$ref": "#task-object"},
        "description": "New tasks to add to the plan"
      },
      "remove": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Task IDs to remove (must be pending, not completed/in-progress)"
      },
      "update": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "id": {"type": "string"},
            "instruction": {"type": "string", "description": "Updated instruction"},
            "depends_on": {"type": "array", "items": {"type": "string"}, "description": "Updated dependencies"}
          },
          "required": ["id"]
        },
        "description": "Updates to pending tasks (instruction, dependencies)"
      },
      "complete": {
        "type": "boolean",
        "description": "Set to true to end planning and synthesize final answer"
      }
    }
  }
}
```

### `read-task-result` tool

The coordinator needs access to full child results when summaries aren't enough. Rather than giving it filesystem access, the compiler injects a `read-task-result` tool that reads from the run-state directory:

```json
{
  "name": "read-task-result",
  "description": "Read the full result from a completed task. Use when the key_findings summary isn't enough detail. Returns the complete output that the agent produced.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "task_id": {
        "type": "string",
        "description": "Task ID from the plan (e.g. 'jira-research', 'config-lookup'). Must be a completed task."
      }
    },
    "required": ["task_id"]
  }
}
```

**How it works:**
- The compiler handles this tool internally — no MCP server or filesystem skill needed
- It reads from `.swarmkit/run-state/<run-id>/<agent-id>.md` and returns the content
- Only completed tasks can be read (pending/failed tasks return an error message)
- The coordinator never touches the filesystem directly

### Result access model

Two layers — summaries by default, full results on demand:

```
Layer 1: Automatic (no tool call needed)
  Checkpoint status message includes 3-5 bullet key_findings per task.
  The coordinator sees these every time it runs.

Layer 2: On demand (read-task-result tool call)
  Coordinator calls read-task-result(task_id="config-lookup")
  → compiler reads .swarmkit/run-state/<run-id>/config-analyst.md
  → returns full content to coordinator's context

Example flow:
  Coordinator sees: "✅ config-lookup: 8 return pipelines found"
  Thinks: "I need the specific pipeline names for the document"
  Calls: read-task-result(task_id="config-lookup")
  Gets: Full 10KB config-analyst output with all pipeline details
```

This keeps the coordinator's default context small (summaries ≈ 500 bytes per task) while giving it access to full results (10-50KB each) only when needed. For a 5-child topology, that's ~2.5KB of summaries vs ~250KB if all results were dumped into context.

### How the coordinator reviews results

At each checkpoint, the coordinator receives a structured summary:

```
TASK PLAN STATUS:

✅ jira-research (jira-researcher) — 300s, 50 tool calls
   Findings:
   - Found 15 Jira tickets related to OMS returns
   - 3 Confluence pages with return process documentation
   - Key ticket PROJ-100: Return Order Processing redesign
   Full results: .swarmkit/run-state/3d1c3d0a/jira-researcher.md

✅ config-lookup (config-analyst) — 140s, 25 tool calls
   Findings:
   - 8 return-related pipelines found
   - PH2_RETURN_PIPELINE: 12 transactions, 5 hub rules
   Full results: .swarmkit/run-state/3d1c3d0a/config-analyst.md

⏳ docs-search (docs-researcher) — pending (depends on: jira-research ✅)
⏳ code-review (sterling-developer) — pending (depends on: config-lookup ✅, docs-search ⏳)
⏳ synthesize (self) — pending (depends on: code-review ⏳)
⏳ write-doc (document-writer) — pending (depends on: synthesize ⏳)

Call update-task-plan to adjust the plan, or wait for pending tasks.
```

The coordinator can then:
- **See jira found data** → keep the plan as-is
- **See jira found nothing** → remove code-review, synthesize, write-doc; add a simple "report no data" self-task
- **Update docs-search instruction** → "Focus on these specific APIs found in Jira: createReturn, processReturn"

### Recursive planning

Every agent with 2+ children is a planner at its level:

```
Sterling topology:
  root (1 child → delegate_to only, no planning)
    └── architect (5 children → creates task plan)
          ├── jira-researcher (0 children → worker)
          ├── config-analyst (0 children → worker)
          ├── docs-researcher (0 children → worker)
          ├── developer (0 children → worker)
          └── document-writer (0 children → worker)

Code review topology:
  root (3 children → creates task plan)
    ├── engineering-leader (3 children → creates sub-plan)
    │     ├── code-reader (worker)
    │     ├── code-reviewer (worker)
    │     └── security-reviewer (worker)
    ├── qa-leader (2 children → creates sub-plan)
    │     ├── test-analyst (worker)
    │     └── qa-judge (worker)
    └── ops-leader (1 child → delegate_to only)
          └── deploy-reviewer (worker)
```

Each planner is independent — the root creates a plan across leaders, each leader creates a sub-plan across its workers. Plans don't nest; they're flat at each level.

### Persistent state on disk

After every task status change, the compiler writes to disk:

```
.swarmkit/run-state/<run-id>/
├── tasks.json              # task plan with status (updated after each task)
├── jira-researcher.md      # full result from jira-researcher
├── config-analyst.md       # full result from config-analyst
├── docs-researcher.md      # full result from docs-researcher
├── sterling-developer.md   # full result from developer
└── synthesize.md           # full result from self-task
```

This serves four purposes:
- **Crash recovery:** if the process dies, results on disk survive
- **Human inspection:** user can read findings during a run (`cat .swarmkit/run-state/*/jira-researcher.md`)
- **Resume:** `--resume` reads tasks.json to know what completed and what's pending
- **Coordinator context:** summaries from tasks.json, full results via `read-task-result` tool

### Summary-first child results

When a child completes, the compiler:
1. Writes the full result to `.swarmkit/run-state/<run-id>/<child-id>.md`
2. Generates 3-5 bullet key findings (one LLM call, cheap model)
3. Stores findings in `tasks.json`
4. Passes only the findings to the coordinator at the next checkpoint

**Cost of summarization:** one additional LLM call per child completion using a cheap model (DeepSeek V4 Flash at $0.14/M). For a 5-child topology, that's 5 extra calls — negligible vs the 200+ tool calls in a typical run, and saves massive context window space.

If the coordinator needs detail, it can call `read-context` with the result path.

### Interaction with existing systems

| System | Change |
|---|---|
| `SwarmState` | Add `task_plan` field (replaces `delegation_counts`) |
| `_build_tools` | Inject `create-task-plan` / `update-task-plan` / `read-task-result` for agents with 2+ children |
| `_build_prompt_messages` | Build context from task plan summaries (automatic, no tool call) |
| `_dispatch_response` | Handle `create-task-plan` / `update-task-plan` / `read-task-result` tool calls |
| `_build_agent_node` | Execute self-tasks with coordinator's own skills |
| `_workspace_runtime.py` | Create run-state directory, persist tasks.json |
| `--resume` | Read tasks.json to rebuild plan state |
| `swarmkit trace` | Show task plan alongside call graph |
| `swarmkit checkpoints` | Show task completion status |
| Agent with 1 child | Unchanged — uses `delegate_to_*` (v1 behavior) |

### What this does NOT change

- **Worker agents** — workers don't know about task plans. They receive instructions and execute.
- **Skill execution** — tools work the same way. Only the coordinator's delegation model changes.
- **LangGraph graph structure** — nodes, edges, routing stay the same. Task execution uses the existing graph.
- **Checkpointing** — LangGraph checkpoints continue as before. Disk-based task state is additive.
- **Topology YAML** — no new fields needed. The compiler detects planners by child count.

### What this replaces

| v1 (current) | v2 (this design) |
|---|---|
| `delegate_to_*` tools (for 2+ children) | `create-task-plan` + `update-task-plan` + `read-task-result` |
| `delegation_counts` in SwarmState | `task_plan` in SwarmState |
| Partial child results message | Structured checkpoint summary |
| Prompt-based "do NOT re-delegate" | Plan-driven execution (no ad-hoc delegation) |
| `SWARMKIT_MAX_DELEGATIONS_PER_CHILD` | Plan updates at checkpoints (follow-up = add new task) |

## Migration

- **Agents with 0-1 children:** no change. `delegate_to_*` works as before.
- **Agents with 2+ children:** automatically get planning tools. Old `delegate_to_*` tools are not injected. If a workspace has hand-written prompts referencing `delegate_to_*`, they'll need updating. But the auto-generated prompt section handles this.
- **Existing `delegation_counts`:** removed from SwarmState. Replaced by task plan status tracking.
- **Backward compatibility:** the compiler checks for `task_plan` in state. If absent (old checkpoints), falls back to v1 behavior.

## Non-goals

- **Per-turn persistence within an agent.** Writing to disk after every tool call adds I/O overhead and complexity. The task plan updates at agent boundaries (start/complete/fail). Mid-agent persistence is a separate future feature.
- **Cross-run learning.** The task plan is per-run. Learning from previous runs is a Rynko feature.
- **Topology-level task plans.** Plans are created at runtime by coordinators, not declared in topology YAML. The topology's `depends_on` field is for agent-level DAGs, not task-level.

## Implementation plan

| PR | Feature | Files | Dependencies |
|----|---------|-------|-------------|
| 1 | `create-task-plan` tool + task plan in SwarmState + disk persistence | `_state.py`, `_compiler.py`, `_workspace_runtime.py` | None |
| 2 | Task execution engine (parallel batches, self-tasks) | `_compiler.py` | PR 1 |
| 3 | `update-task-plan` tool + checkpoint review loop | `_compiler.py` | PR 2 |
| 4 | Summary-first child results (summarization LLM call) + `read-task-result` tool | `_compiler.py` | PR 2 |
| 5 | Init-read pattern + resume from tasks.json | `_build_prompt_messages`, `_workspace_runtime.py` | PR 3 |
| 6 | CLI integration (trace, checkpoints show task status) | `cli/__init__.py` | PR 5 |
| 7 | Remove v1 delegation artifacts (`delegation_counts`, partial results) | `_state.py`, `_compiler.py` | PR 6 |

## References

- Anthropic: [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) — JSON task tracking, progress files, init sequences
- Port.io: [Engineering team skills library](https://thenewstack.io/engineering-team-skills-library/) — self-healing feedback loops
- SwarmKit design §14.3 — compiler architecture
- SwarmKit design `design/details/dag-dependency-graph.md` — DAG execution (reused for task dependencies)
- SwarmKit v1.1.15 — partial child results (predecessor, to be replaced)
- SwarmKit v1.1.17 — delegation count cap (predecessor, to be replaced)

## Open questions

| Question | Options | Recommendation |
|---|---|---|
| Summarization model | Same as child / cheap model / fixed model | Cheap model (DeepSeek V4 Flash) — summarization doesn't need strong reasoning |
| Plan validation | Strict (reject invalid agent IDs) / lenient (warn) | Strict — fail fast on typos in agent IDs |
| Max tasks per plan | Unlimited / capped | Cap at 20 — more than that suggests the topology needs restructuring |
| Self-task tool access | All coordinator skills / subset | All coordinator skills — the coordinator knows what tools it needs |
| Checkpoint frequency | After every task / after every batch | After every task — maximizes crash recovery |
