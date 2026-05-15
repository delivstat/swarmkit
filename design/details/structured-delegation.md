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

### Core concept: task tracker as persistent, structured state

Replace the current `agent_results: dict[str, str]` (free text) with a structured task tracker that:
- Lives on disk at `.swarmkit/run-state/<run-id>/tasks.json`
- Updates after every child agent completion
- Provides summaries to the coordinator (not full results)
- Persists full results as separate files for human inspection

### Architecture

```
Coordinator (architect)
    │
    ├── reads tasks.json on every re-entry
    │   → knows what's done, what's pending, what failed
    │
    ├── delegates to child
    │   → child runs, writes progress to disk after each tool call
    │   → child completes → compiler updates tasks.json
    │   → full result → .swarmkit/run-state/<run-id>/<child-id>.md
    │   → summary (3-5 bullets) → coordinator's context
    │
    └── synthesizes when all tasks done
        → reads summaries from tasks.json
        → reads full results from disk only if needed
```

### Task object schema

```json
{
  "run_id": "3d1c3d0a-51ff-48d7-b9fb-e0cc75d06b73",
  "topology": "sterling-assistant",
  "started_at": "2026-05-15T04:14:04Z",
  "tasks": [
    {
      "id": "jira-research",
      "agent": "jira-researcher",
      "delegated_by": "sterling-architect",
      "status": "completed",
      "delegation_count": 1,
      "started_at": "2026-05-15T04:14:30Z",
      "completed_at": "2026-05-15T04:19:30Z",
      "duration_s": 300,
      "tool_calls": 50,
      "input_summary": "Search for RETN, RITN, Store and DC Returns in Jira and Confluence",
      "key_findings": [
        "Found 15 Jira tickets related to CROMA returns (RETN, RITN)",
        "3 Confluence pages: OMS Return Processes, Return Creation, CROMA Returns",
        "Key ticket RT-727: Return Order Processing redesign",
        "Store returns use RETN fulfillment type, DC returns use RITN",
        "Return pipeline: CROMA_PH2_RETURN_PIPELINE"
      ],
      "result_path": ".swarmkit/run-state/3d1c3d0a/jira-researcher.md"
    },
    {
      "id": "config-lookup",
      "agent": "config-analyst",
      "delegated_by": "sterling-architect",
      "status": "completed",
      "delegation_count": 1,
      "key_findings": [
        "8 return-related pipelines found",
        "CROMA_PH2_RETURN_PIPELINE: 12 transactions, 5 hub rules",
        "Return services: RETURN_MONITOR, DCS_RETURN_RCPT_UPLOAD, etc."
      ],
      "result_path": ".swarmkit/run-state/3d1c3d0a/config-analyst.md"
    },
    {
      "id": "doc-generation",
      "agent": "document-writer",
      "delegated_by": "sterling-architect",
      "status": "failed",
      "error": "deepseek/deepseek-v3-0324 is not a valid model ID",
      "delegation_count": 1
    }
  ]
}
```

### Five implementation features

#### 1. Task tracker in SwarmState

Add `task_tracker: dict[str, Any]` to `SwarmState` alongside existing `agent_results`. The compiler maintains this automatically — agents don't manage it.

```python
# SwarmState extension
task_tracker: Annotated[dict[str, Any], _merge_dicts]
```

The compiler populates `task_tracker` when:
- A delegation starts → add task with `status: "in_progress"`
- A child completes → update to `status: "completed"`, add `key_findings`
- A child fails → update to `status: "failed"`, add `error`

#### 2. Persistent run state on disk

After every task status change, write `tasks.json` to disk:

```
.swarmkit/run-state/<run-id>/
├── tasks.json              # task tracker (updated after each child)
├── jira-researcher.md      # full result from jira-researcher
├── config-analyst.md       # full result from config-analyst
├── docs-researcher.md      # full result from docs-researcher
└── sterling-developer.md   # full result from developer
```

This serves three purposes:
- **Crash recovery:** if the process dies, results on disk survive
- **Human inspection:** user can read findings during a run
- **Resume:** `--resume` reads tasks.json to know what completed

#### 3. Summary-first child results

When a child completes, the compiler:
1. Writes the full result to `.swarmkit/run-state/<run-id>/<child-id>.md`
2. Asks the model to generate 3-5 bullet key findings (one LLM call)
3. Stores findings in `tasks.json`
4. Passes only the findings to the coordinator's context

This replaces the current approach of dumping 50KB of raw text into the coordinator's messages. The coordinator sees:

```
Workers completed:
[jira-researcher]: 
  - Found 15 Jira tickets related to CROMA returns (RETN, RITN)
  - 3 Confluence pages with return process documentation
  - Key ticket RT-727: Return Order Processing redesign
  Full results: .swarmkit/run-state/3d1c3d0a/jira-researcher.md

Workers pending: config-analyst, docs-researcher, sterling-developer
```

If the coordinator needs detail, it can call `read-context` with the result path.

**Cost of summarization:** one additional LLM call per child completion. For a 5-child topology, that's 5 extra calls — negligible vs the 200+ tool calls in a typical run, and saves massive context window space.

#### 4. Status-aware delegation

Replace the blunt `delegation_counts` cap with proper status tracking:

| Task status | Delegation tool available? | Behavior |
|---|---|---|
| `pending` | Yes | Normal delegation |
| `in_progress` | No | Child is running |
| `completed` | Only as `follow_up_<child>` | Must provide new question referencing previous findings |
| `failed` | Yes (for retry) | Coordinator can retry with different parameters |

The `follow_up_<child>` tool name signals to the model that this is a follow-up, not a fresh delegation. The tool description includes the previous findings so the model knows what was already found.

This replaces `SWARMKIT_MAX_DELEGATIONS_PER_CHILD` with a smarter mechanism:
- First delegation: free
- Follow-up: requires a new question (enforced by tool description showing previous findings)
- After 2 follow-ups: tool removed (hard cap preserved as safety net)

#### 5. Init-read pattern

When the coordinator re-enters (after a child returns or on resume), it reads `tasks.json` first:

```python
# In _build_prompt_messages, before building the message list:
task_file = run_state_dir / "tasks.json"
if task_file.exists():
    tasks = json.loads(task_file.read_text())
    # Build context from task summaries, not raw agent_results
```

This mirrors the Anthropic article's "session initialization sequence" — the coordinator always has a structured view of what happened, regardless of context window state.

On `--resume`, this is critical: the coordinator's LLM context is empty (fresh start), but the task tracker tells it exactly what was found and what remains.

### Interaction with existing systems

| System | Change |
|---|---|
| `SwarmState` | Add `task_tracker` field |
| `_build_prompt_messages` | Read from task tracker instead of raw `agent_results` |
| `_dispatch_response` | Write task status on delegation start/completion |
| `_run_tool_loop` | Optionally write per-turn progress to disk |
| `_workspace_runtime.py` | Create run-state directory on run start |
| `--resume` | Read tasks.json to rebuild coordinator context |
| `swarmkit trace` | Show task status alongside call graph |
| `swarmkit checkpoints` | Show task completion status |

### What this does NOT change

- **Agent prompts** — agents don't know about the task tracker. The compiler manages it transparently.
- **Skill execution** — tools work the same way. Only the coordinator's context changes.
- **LangGraph graph structure** — nodes, edges, routing all stay the same.
- **Checkpointing** — LangGraph checkpoints continue as before. The disk-based task tracker is additive.

## Migration

The task tracker is additive — existing workspaces work unchanged. When `task_tracker` is empty (old runs), the compiler falls back to the current `agent_results` behavior.

## Non-goals

- **Per-turn persistence within an agent.** Writing to disk after every tool call adds I/O overhead and complexity. The task tracker updates at agent boundaries (start/complete/fail), not at tool-call granularity. If mid-agent persistence is needed later, it's a separate feature.
- **Cross-run learning.** The task tracker is per-run. Learning from previous runs (e.g. "last time you searched for X and found nothing") is a Rynko feature.
- **Task dependencies.** The task tracker records what happened, not what should happen next. DAG dependencies are still handled by the topology's `depends_on` field.

## Implementation plan

| PR | Feature | Files |
|----|---------|-------|
| 1 | Task tracker in SwarmState + disk persistence | `_state.py`, `_compiler.py`, `_workspace_runtime.py` |
| 2 | Summary-first child results (summarization LLM call) | `_compiler.py` |
| 3 | Status-aware delegation (follow_up tool) | `_compiler.py`, `_build_tools` |
| 4 | Init-read pattern + resume from tasks.json | `_build_prompt_messages`, `_workspace_runtime.py` |
| 5 | CLI integration (trace, checkpoints show task status) | `cli/__init__.py` |

## References

- Anthropic: [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) — JSON task tracking, progress files, init sequences
- Port.io: [Engineering team skills library](https://thenewstack.io/engineering-team-skills-library/) — self-healing feedback loops
- SwarmKit design §14.3 — compiler architecture
- SwarmKit v1.1.15 — partial child results (predecessor)
- SwarmKit v1.1.17 — delegation count cap (to be replaced by status-aware delegation)

## Open questions

| Question | Impact |
|---|---|
| Should the summarization LLM call use the same model as the child or a cheap model? | Cost vs quality. A cheap model (DeepSeek V4 Flash) can summarize 50KB of text into 5 bullets. |
| Should the task tracker be a new MCP server (read-task-status, update-task) or compiler-internal? | If MCP, agents can read their own progress. If compiler-internal, it's simpler but agents are unaware. |
| Should `follow_up_<child>` show the previous findings in the tool description, or as a system message? | Tool description is more visible to the model but adds to tool token count. |
