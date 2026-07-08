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

### Fleet-enrollment protocol schemas (`schemas/protocol/`)

The register/join handshake + `InstanceState` wire contract (design
[19](../../design/details/control-plane/19-fleet-enrollment-protocol.md)) — published so **any
client** can validate against it, not just the SwarmKit runtime/panel. These are API
request/response contracts, a distinct namespace from the artifact schemas above (they are not
user-authored artifacts and are **not** run through pydantic/TS codegen).

| Schema | Purpose |
| --- | --- |
| `instance-state.schema.json` | Full observed state export (`GET /fleet/state`) |
| `credential.schema.json` | The issued opaque, scoped API-key credential |
| `register-request` / `register-response` | Mode A (panel → instance) handshake |
| `join-request` / `join-response` | Mode B (instance → panel) handshake |

Validate them with the dedicated entry points (responses cross-reference the credential +
instance-state schemas by `$id`, so validation resolves those automatically):

```python
from swarmkit_schema import validate_protocol
validate_protocol("register-response", body)   # raises jsonschema.ValidationError on failure
```

```ts
import { validateProtocol } from "@swarmkit/schema";
validateProtocol("join-request", body);          // -> { valid: true } | { valid: false, errors }
```

## Development

```bash
uv sync --package swarmkit-schema          # Python
pnpm --filter @swarmkit/schema install     # TypeScript
```
