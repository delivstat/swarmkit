# reference/

The v1.0 reference artifacts — topologies, archetypes, and skills that ship with Swael itself. These are normal YAML files that live in a user's workspace alongside their own artifacts. The ones here are the canonical examples curated by the project.

## v1.0 scope (design §11, §13)

### Three reference topologies

1. **Code Review Swarm** — multi-leader coordination (Engineering, QA, Operations). Demonstrates A2A handoffs, validation gates, LLM judges, guarded cross-leader channels, mandatory HITL on deploy.
2. **Skill Authoring Swarm** — conversational skill authoring. Conversation Leader + specification workers + Review Leader + Test Execution Leader + Publication Worker.
3. **Workspace Authoring Swarm** — bootstraps a new workspace end-to-end from conversation. The on-ramp topology for `swael init`.

### ~15 archetypes

Coordination (supervisor-leader, judge-and-handoff-leader, conversation-leader, escalation-leader), Analysis (code-analyst-worker, schema-drafter-worker), Generation (code-gen-worker, prompt-writer-worker), Evaluation (llm-judge-worker, security-reviewer-worker, schema-validator-worker), Action (mcp-caller-worker, notification-sender-worker), Retrieval (file-context-worker, vector-search-worker).

### ~20 starter skills

To be derived from the needs of the three reference topologies.

## Layout

```
topologies/     # *.yaml, one file per topology
archetypes/     # *.yaml, one file per archetype
skills/         # *.yaml, one file per skill
```

These files are validated against `packages/schema/schemas/*.schema.json` in CI. Do not add an artifact without a corresponding test in `packages/runtime/tests` that loads it.
