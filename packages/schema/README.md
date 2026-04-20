# swarmkit-schema

Canonical JSON Schema definitions plus language-neutral validators.

The schemas under [`schemas/`](./schemas/) are the **source of truth**. The Python and TypeScript packages consume those files — they do not redefine shape, only wrap validation behaviour for their language's ecosystem.

## Layout

```
schemas/       # Canonical JSON Schema (*.schema.json) — source of truth
python/        # swarmkit-schema (PyPI) — validators + pydantic model codegen target
typescript/    # @swarmkit/schema (npm)   — validators + ts-json-schema-generator target
```

## Design references

- §9.1 — schema is component #3 of the three-package system
- §9.2 — all components validate against this package
- §10 — topology schema (high-level)
- §20.1 Phase 1 — detailed schema specification is the first deliverable

## Schemas (v1.0)

| Schema | Purpose | Design ref |
| --- | --- | --- |
| `topology.schema.json` | A complete swarm definition | §10 |
| `skill.schema.json` | A single capability / decision / coordination / persistence unit | §6.3 |
| `archetype.schema.json` | A kind of agent (noun) | §6.6, §13 |
| `workspace.schema.json` | Workspace config, IAM, shared resources | §9.3 |
| `trigger.schema.json` | Schedules, webhooks, file watches | §5.4 |

Each schema versions independently (`apiVersion: swarmkit/v1`).

## Development

```bash
uv sync --package swarmkit-schema          # Python
pnpm --filter @swarmkit/schema install     # TypeScript
```
