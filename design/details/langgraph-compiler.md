---
title: LangGraph compiler — topology → StateGraph translation
description: How the compiler turns a ResolvedTopology into an executable LangGraph StateGraph. Covers node construction, context model, skill dispatch, and the leader-mediated interaction model.
tags: [runtime, compiler, langgraph, m3]
status: proposed
---

# LangGraph compiler

## Goal

Given a `ResolvedTopology` (the M1 output), produce an executable
LangGraph `StateGraph` that:

1. Creates one node per agent.
2. Routes messages along the hierarchy (parent delegates to children,
   children respond to parent).
3. Dispatches skills as tool calls through the `ModelProvider` interface.
4. Gates every action through `GovernanceProvider.evaluate_action`.
5. Records every step through `GovernanceProvider.record_event`.
6. Checkpoints state to SQLite for resume-after-crash.

The compiler is in `packages/runtime/src/swael_runtime/compiler/`.

**Design reference:** §14.3, §14.5, §5.3, §8.

## Non-goals

- **MCP integration.** M3 uses mock MCP tool responses. Real MCP server
  lifecycle lands in M5.
- **Decision skills (LLM judges).** Tier 2/3 evaluation lands in M4.
- **Streaming to the user.** M3 returns the final state. Streaming UX
  lands with the CLI observability primitives (M4 tasks #35/#36).
- **Eject.** `swael eject` (M9) needs the compiler to produce
  readable code. For M3, the compiler produces a runnable graph object
  in memory, not source code.

## Context model — leader-mediated interaction

This is the most important architectural decision in the compiler.

### Principle

**The topology declares capabilities. The leader decides interactions.**

The YAML says which agents exist, what skills they have, and what
scopes they hold. It does NOT declare which agents talk to which agents
at runtime. The leader (root/supervisor) observes task requirements and
dynamically decides when workers should collaborate.

### Default flow: hierarchical message passing

Every topology starts with this pattern. Parent sends a task message to
a child. Child processes it and returns a result. Parent collects
results from all children and produces its output.

```
User → Root
Root → Worker A: "Do X" (delegation)
Worker A → Root: "X done, here's the result"
Root → Worker B: "Do Y" (delegation)
Worker B → Root: "Y done"
Root → User: "Final answer combining X and Y"
```

Each agent sees only:
- The message its parent sent (the delegation)
- Its own tool/skill results
- Its own conversation history within this execution

The parent controls what context to include in each delegation. This is
**Option C** from the design discussion — minimal context, maximum
governance, simplest compiler.

### Leader-mediated worker collaboration

When a leader determines that two workers need to resolve something
together, it invokes the `coordinate-workers` skill:

```
Leader detects conflict between Worker A and Worker B's results.
Leader invokes: coordinate-workers(
    participants=["worker-a", "worker-b"],
    topic="Reconcile security fix with performance constraint",
    context={a_finding: "...", b_finding: "..."},
)
```

The runtime handles this as follows:

1. **Governance check.** The leader's `coordinate-workers` invocation
   goes through `evaluate_action`. The leader must have the
   `coordination:facilitate` scope. The named participants must be in
   the leader's subtree.

2. **Sub-conversation.** The runtime creates a temporary exchange:
   - Sends Worker A: the topic + context + Worker B's relevant finding
   - Worker A responds
   - Sends Worker B: Worker A's response
   - Worker B responds
   - Repeat until convergence or max rounds (configurable, default 3)

3. **Audit.** Every message in the sub-conversation is recorded through
   `record_event` with `event_type="coordination.exchange"`.

4. **Result.** The converged output returns to the leader as the
   skill's result. The leader then uses it in its own reasoning.

### What the YAML declares vs what the leader decides

| YAML (static, authoring-time) | Leader (dynamic, runtime) |
|---|---|
| Which agents exist and their roles | When to delegate to which child |
| Which skills each agent has | When workers should collaborate |
| Whether a leader can coordinate (`coordinate-workers` skill) | Which workers participate |
| IAM scopes per agent | What context each worker receives |
| Model assignments | When to end a collaboration |

### Why not static YAML interaction declarations?

Declaring "Worker A can talk to Worker B" in YAML means the topology
author must predict every interaction pattern at authoring time. That's
brittle — a code review topology might need reviewers to collaborate on
one PR but work independently on another. Same topology, different
runtime behaviour.

The leader has the full task context. It is the right entity to decide
when collaboration is needed. The YAML author declares the *capability*
("this leader can facilitate collaboration"). The leader decides the
rest.

### Why not full shared state?

Shared state (every agent reads a global dict) violates least-privilege.
Worker B could silently read Worker A's internal scratchpad. The
governance pillar exists to prevent this.

Leader-mediated interaction means every piece of context an agent sees
is traceable to a specific delegation or coordination event. The audit
log can answer "what did Worker B know, and who gave it to them?"

### Future: shared data channels (v1.1)

For topologies where the root-as-relay pattern creates excessive token
overhead (e.g. 10 workers all consuming the same research output), v1.1
introduces opt-in shared channels declared in `runtime_config`:

```yaml
runtime_config:
  shared_channels:
    - id: audience-profile
      writers: [researcher]
      readers: [greeter, summariser]
```

Channels are governance-gated (every read is an `evaluate_action`) and
audited. This is an optimisation, not a change in model — the leader
still orchestrates; channels just reduce re-transmission cost.

Not in M3 scope. Noted here so the state schema leaves room for it.

## State schema

The LangGraph state is a `TypedDict`:

```python
class SwarmState(TypedDict):
    # The user's original input
    input: str
    
    # Per-agent message histories, keyed by agent id.
    # Only the agent's own node and its parent's node access its key.
    agent_messages: dict[str, list[Message]]
    
    # Per-agent results (written by the agent, read by its parent)
    agent_results: dict[str, str | None]
    
    # The final output returned to the user
    output: str | None
    
    # Metadata for checkpointing / resume
    current_agent: str | None
    execution_status: Literal["running", "completed", "failed"]
```

LangGraph reducers handle merge semantics:
- `agent_messages`: per-key append (each agent appends to its own list)
- `agent_results`: per-key overwrite (each agent writes its final result)
- `output`: overwrite (root writes the final answer)

## Node construction

Each agent in the `ResolvedTopology` becomes one LangGraph node.

```python
def _build_agent_node(
    agent: ResolvedAgent,
    model_provider: ModelProvider,
    governance: GovernanceProvider,
) -> Callable[[SwarmState], SwarmState]:
    """Build the node function for one agent."""
    
    async def node_fn(state: SwarmState) -> dict:
        # 1. Governance: evaluate whether this agent can execute
        decision = await governance.evaluate_action(
            agent_id=agent.id,
            action=f"agent:execute",
            scopes_required=frozenset(agent.iam.get("base_scope", [])),
        )
        if not decision.allowed:
            await governance.record_event(AuditEvent(...))
            return {"agent_results": {agent.id: f"DENIED: {decision.reason}"}}
        
        # 2. Build the prompt from agent config + parent's delegation
        messages = _build_messages(agent, state)
        
        # 3. Call the model via ModelProvider
        tools = _agent_tools(agent)  # skills → ToolSpec
        response = await model_provider.complete(
            CompletionRequest(
                model=agent.model["name"],
                messages=messages,
                system=agent.prompt.get("system"),
                tools=tools,
            )
        )
        
        # 4. Handle tool use (agentic loop)
        while response.stop_reason == "tool_use":
            tool_results = await _execute_tools(response, agent, governance)
            messages.extend(_tool_result_messages(response, tool_results))
            response = await model_provider.complete(
                CompletionRequest(model=agent.model["name"], messages=messages, tools=tools)
            )
        
        # 5. Record completion event
        await governance.record_event(AuditEvent(
            event_type="agent.completed",
            agent_id=agent.id,
            ...
        ))
        
        # 6. Return result to state
        result_text = _extract_text(response)
        return {
            "agent_messages": {agent.id: messages},
            "agent_results": {agent.id: result_text},
        }
    
    return node_fn
```

## Edge construction

Edges follow the agent hierarchy:

```python
def _build_edges(topology: ResolvedTopology) -> list[Edge]:
    edges = []
    
    def walk(agent: ResolvedAgent, parent_id: str | None):
        if parent_id:
            # Parent delegates to child, child returns to parent
            edges.append(Edge(parent_id, agent.id))  # delegation
            edges.append(Edge(agent.id, parent_id))   # return
        for child in agent.children:
            walk(child, agent.id)
    
    walk(topology.root, None)
    return edges
```

The root node is the entry point. The root decides (via its model call)
which children to delegate to. LangGraph's conditional edges route
based on the root's tool calls (delegation = tool call to
`delegate_to_worker`).

## Skill dispatch

Skills surface as tools to the agent's model. The `_agent_tools`
function maps resolved skills to `ToolSpec` objects:

```python
def _agent_tools(agent: ResolvedAgent) -> list[ToolSpec]:
    tools = []
    for skill in agent.skills:
        tools.append(ToolSpec(
            name=skill.id,
            description=skill.metadata.description,
            input_schema=_skill_input_schema(skill),
        ))
    # Leaders with children get a delegation tool
    if agent.children:
        for child in agent.children:
            tools.append(ToolSpec(
                name=f"delegate_to_{child.id}",
                description=f"Delegate a task to {child.id} ({child.role})",
                input_schema={"type": "object", "properties": {"task": {"type": "string"}}},
            ))
    return tools
```

When the model returns a `tool_use` block:
- If the tool name matches a skill → execute the skill (mock MCP in M3)
- If the tool name matches `delegate_to_<child>` → route to that
  child's node in the graph
- If the tool name is `coordinate_workers` → run the sub-conversation
  handler

## Coordination skill handler

When a leader invokes `coordinate_workers`:

```python
async def _handle_coordination(
    participants: list[str],
    topic: str,
    context: dict,
    agents: dict[str, ResolvedAgent],
    model_provider: ModelProvider,
    governance: GovernanceProvider,
    max_rounds: int = 3,
) -> str:
    """Run a temporary multi-round exchange between workers."""
    
    # Governance gate
    # ... evaluate_action with coordination:facilitate scope ...
    
    conversation: list[Message] = [
        Message(role="user", content=f"Topic: {topic}\nContext: {context}")
    ]
    
    for round_num in range(max_rounds):
        for agent_id in participants:
            agent = agents[agent_id]
            response = await model_provider.complete(
                CompletionRequest(
                    model=agent.model["name"],
                    messages=conversation,
                    system=agent.prompt.get("system"),
                )
            )
            result = _extract_text(response)
            conversation.append(Message(role="assistant", content=f"[{agent_id}]: {result}"))
            
            await governance.record_event(AuditEvent(
                event_type="coordination.exchange",
                agent_id=agent_id,
                payload={"round": round_num, "topic": topic},
            ))
    
    # Return the final exchange as the skill result
    return conversation[-1].content
```

The leader sees the converged output as a tool result, just like any
other skill. The workers' internal exchange is audited but doesn't
pollute the leader's main conversation.

## Checkpointing

LangGraph's built-in `SqliteSaver` checkpoints state after each node
execution:

```python
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string(".swael/state/hello.db")
graph = compiled_graph.compile(checkpointer=checkpointer)
```

`swael run --resume` loads the last checkpoint and continues from
where execution stopped. Useful for long-running swarms that crash
mid-execution.

## `swael run` CLI

```
swael run <workspace> <topology> [--input "..."] [--resume] [--no-color]
```

- Resolves the workspace (reuses M1 `resolve_workspace`)
- Finds the named topology
- Builds the `ProviderRegistry` from workspace config
- Instantiates `GovernanceProvider` (AGT or mock based on config)
- Compiles the topology to a `StateGraph`
- Invokes with the user's input
- Prints the final output

## Structured output governance (M4, architectural note)

Skills that declare an `outputs` block in their YAML get deterministic
output validation in the compiler's tool-use loop. This is an
architectural commitment — the compiler is where it's enforced, even
though the design note lands in M4.

### Why — the Rynko insight

Production experience with Rynko gate validation shows that
**structured constraints + field-specific error feedback** eliminates
most hallucination without any LLM judge. When a model is constrained
to produce `{verdict: "pass"|"fail", confidence: 0.0-1.0}` via
structured generation, and the response is validated against the
schema, shape-level hallucination is impossible. The remaining errors
(wrong value, not wrong shape) are caught by deterministic business
rules and fed back as targeted corrections.

### The four-tier output governance model

| Tier | What | Cost | When |
|---|---|---|---|
| 0 | **Structured generation** — provider JSON mode / tool_use constrains the model at generation time | Zero extra | Always, when skill declares `outputs` |
| 1 | **Schema validation** — JSON Schema check on the response | Near-zero | Always, after model response |
| 2 | **Business rules** — deterministic field-level checks (ranges, enums, cross-field consistency) | Near-zero | When skill declares `validation_rules` |
| 3 | **LLM judge** — semantic evaluation against a rubric | Tokens | When configured (Tier 2/3 judicial) |

### Auto-correction via field-specific errors

When Tier 1 or 2 validation fails, the error is field-specific:
`"confidence must be between 0 and 1, got 1.5"`. The compiler feeds
this back to the model as a tool_result error in the agentic loop:

```
Model returns: {verdict: "pass", confidence: 1.5}
Validation: FAIL — confidence out of range [0, 1]
Re-prompt: "Validation error on field 'confidence': must be between 0 and 1, got 1.5. Correct this field."
Model returns: {verdict: "pass", confidence: 0.85}
Validation: PASS
```

The model corrects one field — not the entire response. This is:
- **Cheaper** than regenerating from scratch (fewer tokens)
- **More reliable** than a generic "try again" (the error is specific)
- **Deterministic** in the validation step (no LLM judge needed for
  shape/range errors)

The retry budget is configurable per skill (`max_retries`, default 2).
If the model can't produce a valid response after retries, it
escalates to the judicial pillar (Tier 3 LLM judge or HITL).

### Where this lives in the compiler

In the agentic tool-use loop inside `_build_agent_node`:

```
1. Model call (with structured generation if skill has outputs)
2. Parse response
3. If tool_use → execute tool → validate output → if invalid, re-prompt with field errors
4. If text → return (no output governance on free-text responses)
```

Output governance only fires for skills with declared `outputs` blocks.
Free-text agent responses (e.g. the root's final answer to the user)
are not schema-validated — they're evaluated by Tier 3 judges if
configured.

Implementation lands in M4. This section is an architectural
commitment so the compiler's loop design leaves room for it.

## Implementation plan (PRs)

1. **This PR:** design note only.
2. **PR 2:** compiler core — `compile_topology()` → `StateGraph`. Node
   construction, edge construction, delegation tool dispatch. Uses
   `MockModelProvider` + `MockGovernanceProvider`. Tests assert graph
   structure and mock execution flow.
3. **PR 3:** governance middleware — every node calls `evaluate_action`
   before executing and `record_event` after. Tests assert deny → audit
   flow through a compiled graph.
4. **PR 4:** coordination skill handler + agentic tool-use loop.
5. **PR 5:** `swael run` CLI + SQLite checkpointing + exit demo.

## Exit demo

Two-agent hello-swarm topology:
1. `swael run examples/hello-swarm/workspace hello --input "Greet the engineering team"`
2. Root delegates to greeter worker.
3. Worker produces a greeting.
4. Root returns the final greeting to the user.
5. Checkpoint file created at `.swael/state/hello.db`.
6. `swael run ... --resume` picks up from checkpoint.

## Test plan

- **Compiler unit tests:** given a `ResolvedTopology` with N agents,
  the compiled graph has N nodes + correct edges. Tested with mock
  providers.
- **Delegation test:** root agent delegates to a worker via
  `delegate_to_<child>` tool call. Mock model returns a tool_use block
  targeting the worker, worker's mock model returns a text result, root
  collects it.
- **Governance deny test:** agent with insufficient scopes → node
  returns DENIED result, audit event recorded.
- **Coordination test:** leader invokes `coordinate_workers`, two mock
  workers exchange messages for 2 rounds, converged result returns to
  leader.
- **Checkpoint test:** run to completion, verify `.db` file exists.
  Corrupt mid-run, resume from checkpoint, verify completion.
- **CLI integration:** `swael run` on the hello-swarm example exits
  0, prints a greeting, creates a checkpoint file.
