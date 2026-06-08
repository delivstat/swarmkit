# Level 6: Structured Delegation

Move from simple "delegate and hope" to structured task plans — coordinators create plans, workers execute tasks in dependency order, results are crash-resilient.

## What you'll learn

- Task plans with `create-task-plan`
- Scopes with `create-scope` and `read-scope`
- Two-phase planning (research → scope → targeted tasks)
- Dual model support (cheap tools, quality synthesis)
- Synthesis configuration

## Why structured delegation?

In Level 4, the coordinator just delegated to children and hoped for the best. With structured delegation:
- The coordinator creates a **plan** with ordered tasks
- Independent tasks run **in parallel**
- Dependent tasks wait for their dependencies
- Plans are **crash-resilient** — saved to disk, resumed on restart
- Workers produce **structured results** that the coordinator can synthesize

## Build it

### 1. Enable structured delegation in a topology

```yaml
# topologies/structured-review.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  id: structured-review
  name: Structured Code Review
  description: >
    Coordinator creates a task plan, workers execute in parallel,
    results are synthesized into a final review.
runtime:
  planning:
    scope_required: true
    two_phase: true
agents:
  root:
    id: review-coordinator
    role: root
    archetype: coordinator
    prompt:
      system: |
        You are a code review coordinator. When given code to review:
        1. First call create-scope to define what you're reviewing
        2. Then call create-task-plan to break the review into tasks
        3. Workers will execute the tasks
        4. You synthesize the results into a final review

        Available workers:
        - security-reviewer: checks for vulnerabilities
        - quality-reviewer: checks code quality and patterns
        - test-reviewer: checks test coverage
    children:
      - id: security-reviewer
        role: worker
        archetype: researcher
        prompt:
          system: |
            You are a security reviewer. Analyze code for:
            - SQL injection, XSS, CSRF vulnerabilities
            - Hardcoded secrets or credentials
            - Insecure dependencies
            Return findings as a structured list.
      - id: quality-reviewer
        role: worker
        archetype: researcher
        prompt:
          system: |
            You review code quality. Check for:
            - Clean architecture patterns
            - DRY violations
            - Error handling gaps
            - Naming conventions
      - id: test-reviewer
        role: worker
        archetype: researcher
        prompt:
          system: |
            You review test coverage. Check:
            - Are critical paths tested?
            - Edge cases covered?
            - Test quality (not just quantity)
```

### 2. How it works at runtime

When you run this topology:

```bash
swarmkit run . structured-review \
  --input "Review this Python function: def login(user, pwd): return db.query(f'SELECT * FROM users WHERE name={user} AND pass={pwd}')"
```

The coordinator:
1. Calls `create-scope` → defines the review boundaries
2. Calls `create-task-plan` → creates tasks for each worker
3. The compiler executes independent tasks in parallel
4. Results are collected and synthesized

### 3. Dual model support

Use a cheap model for tool calls and a quality model for the final synthesis:

```yaml
# archetypes/coordinator.yaml — updated
defaults:
  model:
    provider: openrouter
    name: deepseek/deepseek-v4-pro         # synthesis model
    temperature: 0.3
    max_tokens: 4096
    tool_model: moonshotai/kimi-k2.5       # cheap tool-calling model
    tool_provider: openrouter
```

The tool loop (searching, function calls) uses `kimi-k2.5` (cheap, fast). The final response uses `deepseek-v4-pro` (quality). This can reduce costs 60-80%.

### 4. Synthesis configuration

Control how the final output is produced:

```yaml
# topologies/structured-review.yaml — add synthesis block
runtime:
  planning:
    scope_required: true
    two_phase: true
  synthesis:
    provider: openrouter
    model: deepseek/deepseek-v4-pro
    prompt: |
      You are synthesizing a code review from multiple specialists.
      Combine their findings into a single, actionable review.
      Prioritize critical issues first. Use this format:

      ## Critical Issues
      ## Recommendations
      ## Summary
```

### 5. Resume from crash

Structured delegation saves task plans to `.swarmkit/run-state/current/tasks.json`. If a run crashes mid-execution:

```bash
# This detects the previous plan and resumes from where it left off
swarmkit run . structured-review \
  --input "Review the code" \
  --resume
```

### 6. Check task plan status

```bash
# See checkpointed runs
swarmkit checkpoints .

# Trace a specific run
swarmkit trace <run-id> -w .
```

## Planning modes

| Mode | Config | Behavior |
|------|--------|----------|
| Simple | (default) | Coordinator delegates freely |
| Scope required | `scope_required: true` | Must call `create-scope` before synthesis |
| Two-phase | `two_phase: true` | Research first, then scope, then targeted tasks |

## Your workspace so far

```
my-swarm/
├── workspace.yaml
├── archetypes/
│   └── coordinator.yaml    # now with dual model
├── skills/
├── servers/
└── topologies/
    └── structured-review.yaml
```

## Next

[Level 7: Governance & Safety](07-governance.md) — add guardrails that prevent agents from going wrong.
