# Topology

A **topology** is a first-class SwarmKit artifact (`kind: Topology`) that defines a complete swarm: a tree of agents rooted at a single `root`, plus its runtime, artifact, governance, and monitoring configuration. Topology is the framework's core "topology-as-data" claim — a swarm is this file, interpreted by the runtime, not code.

See the [topology schema design note](https://github.com/delivstat/swarmkit/blob/main/design/details/topology-schema-v1.md). This page is the artifact reference.

## Fields

Required top-level: `apiVersion`, `kind`, `metadata`, `agents`. `metadata` requires `name` (lowercase-kebab) and `version` (semver).

| Top-level | Required | What it does |
|---|---|---|
| `agents` | yes | Exactly one `root` agent, which nests `children`. The whole swarm is one tree, one parent per agent. |
| `runtime` | no | Execution config: `mode` (`one-shot`/`persistent`/`scheduled`), `max_concurrent_tasks`, `task_timeout_seconds`, `planning`, `synthesis`, `checkpointing.storage` (`sqlite`/`postgres`). |
| `artifacts` | no | `knowledge_bases`, `review_queues`, `audit` (`level`/`storage`/`retention_days`), and `skill_gap_logging`. |
| `intent_monitoring` | no | Semantic drift detection: `enabled`, `threshold` (default 0.75), `on_drift` (`log`/`warn`/`nudge`). |
| `governance` | no | `decision_skills[]` bindings that override or extend workspace-level bindings by id. |

### Agent fields

| Field | Required | What it does |
|---|---|---|
| `id` | yes | Lowercase-kebab agent id. |
| `role` | yes | `root` (only the top agent) \| `leader` \| `worker`. |
| `archetype` | no | Archetype id this agent instantiates (resolved against the workspace). |
| `model` | no | `provider`, `name`, `temperature`, `max_tokens`, plus dual-model `tool_provider`/`tool_model` and provider-native `options`. |
| `prompt` | no | `system` / `persona`. |
| `skills` | no | Skill IDs — **replaces** the archetype's skill list when present. |
| `skills_additional` | no | Skill IDs **merged onto** the archetype defaults. |
| `iam` | no | `base_scope` / `elevated_scopes`. |
| `output_schema` | no | JSON Schema for structured output (overrides the archetype default), or `null` to opt out. |
| `funnel` | no | A [Funnel](funnel.md) id — a reusable per-artifact quality gate on this agent's output. |
| `children` | no | Nested agents (`leader`/`worker`), each of which may also declare `depends_on` (agent IDs that must finish first — DAG ordering). |

## Schema shape

```yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: <lowercase-kebab>        # required
  version: 1.0.0                 # required, semver
  description: <optional>
runtime:
  mode: one-shot                 # one-shot | persistent | scheduled
  max_concurrent_tasks: 4
agents:
  root:
    id: supervisor
    role: root                   # the root agent must be role: root
    archetype: supervisor-leader
    children:
      - id: analyst
        role: worker
        archetype: code-analyst
        skills_additional: [query-swarmkit-docs]
      - id: writer
        role: worker
        archetype: document-writer
        depends_on: [analyst]    # runs after analyst completes
        funnel: design-signoff   # gate this agent's output
```

## Minimal example

```yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: hello-swarm
  version: 1.0.0
agents:
  root:
    id: assistant
    role: root
    model:
      provider: anthropic
      name: claude-sonnet-4-5
```

## Authoring a topology

`get_schema("topology")` returns the exact shape. Skills are referenced by **id**, never inlined; `skills` replaces archetype defaults while `skills_additional` extends them. Only the `root` agent may have `role: root`. Any agent that produces a sign-off-worthy artifact should reference a `funnel`; any agent that must run after another should declare `depends_on`.

## See also

- [Archetypes catalogue](archetypes.md) · [Skills](skills.md) — what agents instantiate and invoke.
- [Funnel](funnel.md) — the per-artifact gate an agent's output can pass through.
- [Environment configuration](env-config.md) — `${VAR}` references resolve in topology YAML like any artifact.
