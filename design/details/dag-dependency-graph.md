# DAG Dependency Graph for Agent Topologies

**Status:** Design note — planned feature  
**Design ref:** §5.2 (agent hierarchy), §10 (topology schema), §14.3 (LangGraph compiler)

## Problem

SwarmKit topologies currently support tree-based delegation — a parent
delegates to children, children return to parent. The ordering is
either prompt-driven (the root's system prompt says "send to A first,
then B") or hierarchy-driven (leader delegates to workers).

This works for most cases but breaks down for pipelines with strict
ordering requirements:

- Content pipeline: researcher MUST complete before writer starts
- CI/CD flow: build MUST pass before test runs, test MUST pass
  before deploy
- Analysis: data collector and data validator MUST both complete
  before the analyst runs
- Review: author writes, reviewer reviews, editor edits — strict
  sequence

Prompt-driven sequencing is fragile for these — the model might
reorder steps, skip dependencies, or try to run things in parallel
when they shouldn't be.

## Solution

Add `depends_on` to the agent schema. Agents with dependencies only
run after all specified agents have completed with valid results.

```yaml
agents:
  root:
    id: root
    role: root
    model:
      provider: openrouter
      name: meta-llama/llama-3.3-70b-instruct
    children:
      - id: researcher
        role: worker
        archetype: domain-researcher

      - id: writer
        role: worker
        archetype: blog-writer
        depends_on: [researcher]

      - id: reviewer
        role: worker
        archetype: content-reviewer
        depends_on: [writer]

      - id: editor
        role: worker
        archetype: content-editor
        depends_on: [reviewer]
```

This declares: researcher → writer → reviewer → editor. The runtime
enforces the order regardless of what the root model decides.

### Parallel + sequential mixed

```yaml
children:
  - id: doc-searcher
    role: worker
    archetype: doc-specialist

  - id: code-searcher
    role: worker
    archetype: code-specialist

  - id: analyst
    role: worker
    archetype: analyst
    depends_on: [doc-searcher, code-searcher]

  - id: report-writer
    role: worker
    archetype: report-writer
    depends_on: [analyst]
```

This runs doc-searcher and code-searcher in parallel (no deps), then
analyst once both complete, then report-writer after analyst.

```
doc-searcher ──┐
               ├──→ analyst ──→ report-writer
code-searcher ─┘
```

## Schema change

Add `depends_on` to the `child_agent` definition in
`topology.schema.json`:

```json
"child_agent": {
  "allOf": [
    { "$ref": "#/$defs/agent" },
    {
      "properties": {
        "role": { "enum": ["leader", "worker"] },
        "depends_on": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Agent IDs that must complete before this agent runs."
        }
      }
    }
  ]
}
```

## Compiler change

### Current flow (tree-based)

```
Root model call → delegate_to_X tool call → route to X → X runs → return to root
```

The root decides ordering dynamically via tool calls.

### New flow (DAG-based)

When `depends_on` is declared, the compiler builds a dependency graph
alongside the delegation tree:

```python
def _compile_dag_edges(root, agents, graph):
    """Add dependency-based edges to the graph."""
    for agent in agents.values():
        deps = getattr(agent, 'depends_on', None) or []
        if deps:
            for dep_id in deps:
                graph.add_edge(dep_id, agent.id)
```

### Execution modes

**Mode 1: Root-delegated (current, no deps)**

Root explicitly calls `delegate_to_X`. Runtime routes to X. No change.

**Mode 2: Auto-dispatched (with deps)**

When a topology has `depends_on` declarations, the root doesn't need
to delegate explicitly. The runtime auto-dispatches agents based on
dependency resolution:

1. Find all agents with no unmet dependencies → run them (parallel)
2. When an agent completes, check which agents now have all deps met
3. Run newly unblocked agents
4. Repeat until all agents complete
5. Return all results to root for final synthesis

The root's role shifts from "active delegator" to "final synthesiser."

**Mode 3: Hybrid**

Some agents have `depends_on`, others don't. The root can still
delegate explicitly to agents without deps, while the runtime
auto-dispatches the rest based on the graph.

### Router implementation

```python
def _dag_router(state, agents_with_deps):
    """Route to the next runnable agent based on dependency graph."""
    results = state.get("agent_results", {})
    completed = set(results.keys())

    for agent in agents_with_deps:
        deps = set(agent.depends_on)
        if agent.id not in completed and deps.issubset(completed):
            return agent.id  # all deps satisfied

    # All agents complete or blocked
    return "__synthesise__"  # route to root for final answer
```

### Input passing

When an agent with deps runs, its input includes the outputs of its
dependencies:

```python
dep_results = {
    dep_id: results[dep_id]
    for dep_id in agent.depends_on
    if dep_id in results
}
child_state = {
    "input": f"Based on these findings:\n{dep_results}\n\nYour task: {task}",
    ...
}
```

This ensures each agent sees what its predecessors produced.

## Validation

At compile time, the resolver validates:

1. **No cycles** — `depends_on` cannot form circular dependencies
2. **All references valid** — every ID in `depends_on` must exist as
   a sibling agent
3. **No self-reference** — agent cannot depend on itself
4. **Root cannot have deps** — the root agent runs first always

```python
def _validate_dag(agents):
    """Detect cycles and invalid references in depends_on."""
    # Topological sort — if it fails, there's a cycle
    visited = set()
    in_progress = set()

    def visit(agent_id):
        if agent_id in in_progress:
            raise ResolutionError(f"Cycle detected involving {agent_id}")
        if agent_id in visited:
            return
        in_progress.add(agent_id)
        for dep in agents[agent_id].depends_on:
            if dep not in agents:
                raise ResolutionError(f"{agent_id} depends on unknown agent {dep}")
            visit(dep)
        in_progress.remove(agent_id)
        visited.add(agent_id)

    for agent_id in agents:
        visit(agent_id)
```

## Coexistence with current patterns

DAG dependencies and delegation-based routing coexist:

| Feature | Tree delegation | DAG dependencies |
|---------|----------------|-----------------|
| Who decides order | The root model | The YAML declaration |
| When to use | Flexible Q&A, exploration | Strict pipelines |
| Parallel execution | Root calls multiple delegates | Agents with no deps run in parallel |
| Input to agent | Delegation task text | Predecessor outputs |
| Root's role | Active orchestrator | Final synthesiser |

A topology can mix both: some agents delegated by root, others
auto-dispatched by deps. Agents without `depends_on` behave exactly
as they do today.

## Example topologies

### Content pipeline

```yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: content-pipeline
agents:
  root:
    id: root
    role: root
    model:
      provider: openrouter
      name: meta-llama/llama-3.3-70b-instruct
    prompt:
      system: |
        You coordinate content creation. Your workers run
        automatically in dependency order. Review the final
        output from the editor and present it to the user.
    children:
      - id: researcher
        role: worker
        archetype: trend-researcher
      - id: writer
        role: worker
        archetype: blog-writer
        depends_on: [researcher]
      - id: seo-reviewer
        role: worker
        archetype: seo-reviewer
        depends_on: [writer]
      - id: editor
        role: worker
        archetype: content-editor
        depends_on: [writer]
      - id: publisher
        role: worker
        archetype: blog-publisher
        depends_on: [seo-reviewer, editor]
```

### Parallel research + synthesis

```yaml
children:
  - id: web-researcher
    role: worker
    archetype: web-searcher
  - id: doc-researcher
    role: worker
    archetype: doc-searcher
  - id: code-analyst
    role: worker
    archetype: code-analyst
  - id: synthesiser
    role: worker
    archetype: research-synthesiser
    depends_on: [web-researcher, doc-researcher, code-analyst]
```

## Cost implications

DAG-based execution is more predictable than delegation-based:

- Every agent runs exactly once (no re-delegation loops)
- Parallel agents run concurrently (same as parallel delegation)
- No wasted root model calls for sequencing decisions
- The root only runs twice: once to start, once to synthesise

For a 5-agent pipeline, delegation-based might need 5+ root calls
(one per delegation round). DAG-based needs 2 root calls total.

## Implementation estimate

| Component | Effort |
|-----------|--------|
| Schema: add `depends_on` to topology.schema.json | 1 hour |
| Schema: Python + TypeScript codegen | 1 hour |
| Resolver: validate DAG (cycles, references) | 2-3 hours |
| Compiler: dependency-based routing edges | 4-6 hours |
| Compiler: auto-dispatch with input passing | 4-6 hours |
| Tests: unit + integration | 4-6 hours |
| Reference topology: content pipeline | 2-3 hours |
| **Total** | **2-3 days** |

## Open questions

1. Should `depends_on` agents pass their full output or a summary
   to the dependent? Full output could be large; summary loses detail.
2. What happens when a dependency fails? Options: skip the dependent,
   run it with an error note, or fail the whole pipeline.
3. Should the root see intermediate results as they complete, or only
   the final output?
4. Can `depends_on` cross topology boundaries? (Probably not in v1.)
