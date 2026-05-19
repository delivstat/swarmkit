---
title: Two-phase execution flow — correct architectural approach
description: How freeze-scope + update-task-plan should interact with the compiler's state machine. Fixes the infinite loop problem from v1.2.19.
tags: [compiler, task-plan, scope-freeze, architecture]
status: proposed
---

# Two-phase execution flow — correct architecture

## Problem statement

The two-phase planning pattern requires:
1. Phase 1: jira-researcher runs alone
2. Checkpoint: architect reads result, calls freeze-scope, calls update-task-plan to add Phase 2 tasks
3. Phase 2: new tasks execute
4. Synthesis

The v1.2.19 attempt broke because:
- `update-task-plan` was handled in the tool loop as a text-returning tool
- The tool loop doesn't execute tasks — it just handles tool calls
- The model got "10 total, 9 pending" back but tasks never ran
- Model called update-task-plan again trying to trigger execution → infinite loop

## Root cause analysis

The compiler has two execution contexts for the architect:

### Context A: State machine (graph node re-entry)

```
_build_agent_node called
  → sees __task_plan_executing__ state
  → calls execute_task_batch()
  → returns state dict with updated plan
  → router routes back to agent (or END)
```

This is where tasks actually execute. State changes (`__task_plan_created__`, `__task_plan_updated__`, `__task_plan_executing__`) drive the router.

### Context B: Tool loop (within a single agent turn)

```
Model produces tool calls
  → tool loop handles them (read-task-result, freeze-scope, MCP tools)
  → returns results to model
  → model produces next response
  → repeat until model outputs text
```

This is where the model interacts with tools. It does NOT execute tasks or change graph state.

**The bug:** `update-task-plan` is a Context A operation (state change) that was placed in Context B (tool loop). It updated the disk but not the state, so the execution engine never picked up the new tasks.

## Correct design

`update-task-plan` must produce a **state change** that the router recognizes. It cannot be a text-returning tool in the loop.

### The flow

```
Phase 1 task completes
  → all_done() == True, no self-task
  → architect gets "Research phase complete" prompt
  → architect's model response contains:
      tool_calls: [read-task-result, freeze-scope, update-task-plan]
  
_dispatch_response processes the response:
  1. _handle_task_plan_tools sees update-task-plan
  2. Updates plan, returns state: {agent_results: {id: "__task_plan_updated__"}}
  3. Router sees __task_plan_updated__ → routes back to agent
  4. Agent re-enters → sees updated plan → execute_task_batch() runs Phase 2

But freeze-scope and read-task-result also need to run BEFORE
update-task-plan takes effect...
```

### The ordering problem

The model calls three tools in one turn:
1. `read-task-result` (needs tool loop handling)
2. `freeze-scope` (needs tool loop handling)
3. `update-task-plan` (needs state machine handling)

These are in the SAME response. Currently `_handle_task_plan_tools` runs first and if it finds `update-task-plan`, it returns immediately — skipping the other tools.

### Solution: sequential processing within _handle_task_plan_tools

`_handle_task_plan_tools` should process ALL tools in the response in order:
1. `read-task-result` → execute, store result (for model context)
2. `freeze-scope` → execute, write scope.json
3. `update-task-plan` → execute, return state change

The key insight: `_handle_task_plan_tools` already iterates over all blocks. It just needs to handle `read-task-result` and `freeze-scope` inline (as side effects) before returning the state-changing result from `update-task-plan`.

### Implementation

```python
def _handle_task_plan_tools(response, agent, agent_id, state):
    """Handle all planning-related tools in a single response.
    
    Processes tools in order:
    - read-task-result: reads from disk, no state change
    - freeze-scope: writes scope.json, no state change
    - create-task-plan: creates plan, returns state change
    - update-task-plan: updates plan, returns state change
    
    The first state-changing tool (create/update) terminates
    processing and returns the new state.
    """
    # First pass: handle non-state-changing tools
    for block in response.content:
        if block.tool_name == "read-task-result":
            _handle_read_task_result_inline(block, agent_id)
        elif block.tool_name == "freeze-scope":
            _handle_freeze_scope_inline(block, agent_id)
    
    # Second pass: handle state-changing tools
    for block in response.content:
        if block.tool_name == "create-task-plan":
            return _create_plan_state(block, agent, agent_id, state)
        elif block.tool_name == "update-task-plan":
            return _update_plan_state(block, agent, agent_id, state)
    
    return None  # no state-changing tool found
```

### What changes in the tool loop

The tool loop goes back to how it was before v1.2.19:
- `create-task-plan`: skip (handled by _handle_task_plan_tools)
- `update-task-plan`: skip (handled by _handle_task_plan_tools)
- `freeze-scope`: handle inline (write scope.json, return result)
- `read-task-result`: handle inline (read from disk, return result)

The tool loop only handles `freeze-scope` and `read-task-result`.
`update-task-plan` is NEVER in the tool loop.

### When does the model call update-task-plan?

After the Phase 1 checkpoint prompt ("Research phase complete. Call read-task-result, freeze-scope, update-task-plan"), the model produces a response with all three tool calls. `_dispatch_response` calls `_handle_task_plan_tools` FIRST, which:
1. Handles read-task-result (reads file, logs)
2. Handles freeze-scope (writes scope.json, logs)
3. Finds update-task-plan → returns `__task_plan_updated__` state

The tool loop never starts because `_handle_task_plan_tools` returned a state dict.

### What if the model calls them across multiple turns?

Turn 1: model calls `read-task-result` only
  → _handle_task_plan_tools returns None (no state change)
  → _dispatch_response goes to tool loop
  → tool loop handles read-task-result, returns result
  → model gets result, produces Turn 2

Turn 2: model calls `freeze-scope` + `update-task-plan`
  → _handle_task_plan_tools runs:
    - handles freeze-scope (writes scope.json)
    - finds update-task-plan → returns state
  → tool loop never starts
  → state returned to graph

This works! The model can spread tools across turns and it still
works correctly — update-task-plan always produces a state change
regardless of when it's called.

### What if the model calls ONLY read-task-result and freeze-scope (no update-task-plan)?

This is what happened in v1.2.18 — the model narrated instead of
calling update-task-plan, got stripped, and returned empty.

Fix: the Phase 1 checkpoint prompt already says "call update-task-plan."
If the model doesn't call it after reading results + freezing scope,
the forced synthesis kicks in and the model must either:
- Call update-task-plan (state change → execution continues)
- Or produce a final text response (synthesis based on Phase 1 only)

The second case is acceptable for simple queries that don't need
Phase 2. The prompt says "OR write the final response if this was
a simple query that needs no further research."

## Summary of changes needed

1. **_handle_task_plan_tools:** Add handling for `read-task-result`
   and `freeze-scope` as side-effect-only operations (no state
   change). Process them BEFORE the state-changing tools.

2. **_tool_loop.py:** Revert `update-task-plan` handling (keep it
   skipped). Keep `freeze-scope` handling (it's non-state-changing).

3. **Prompt:** Keep the Phase 1 checkpoint prompt as-is ("call
   read-task-result, freeze-scope, update-task-plan").

4. **freeze-scope result text:** Remove the "NOW call update-task-plan"
   instruction (it's in the prompt already, and the model might
   call both in the same turn anyway).

## Why this works

- `update-task-plan` always produces a state change → compiler
  always picks up new tasks and executes them
- `freeze-scope` and `read-task-result` run as side effects in
  both contexts (tool loop OR _handle_task_plan_tools) — they
  don't need state changes
- The model can call all three in one turn OR across multiple
  turns — both paths work
- No infinite loops — update-task-plan returns a state, not a
  text result that the model responds to

## Implementation plan

Single PR:
1. Update `_handle_task_plan_tools` to process read-task-result +
   freeze-scope as side effects before state-changing tools
2. Revert update-task-plan from tool loop (already done in v1.2.20)
3. Keep freeze-scope in tool loop (for cases where model calls it
   without update-task-plan in the same turn)
4. Test: model calls all three in one turn → state change produced
5. Test: model calls read+freeze in turn 1, update in turn 2 → works
