---
title: Canary deployments — topology-level version routing
description: Weighted traffic splitting between topology versions with automatic promotion based on drift, error rate, and run count metrics.
tags: [serve, deployment, observability]
status: implementing
---

# Canary deployments

## Problem

When updating a topology (changing prompts, swapping models, adding agents),
there's no way to gradually roll out the change. It's all-or-nothing: replace
the topology file and every request hits the new version. If the new version
has higher drift, worse output quality, or unexpected errors, all users are
affected before the problem is detected.

## Design

### Core concept

A workspace can register multiple **versions** of the same topology. The
server routes each incoming request to a version based on configurable
**weights**. Metrics from each version feed **promotion criteria** that
can automatically shift traffic from the old version to the new one.

### Topology versioning

Topologies already have `metadata.version` (semver). For canary deployments,
the workspace places multiple versions of the same topology in a
`topologies/` subdirectory:

```
workspace/
  topologies/
    hello/
      hello.yaml          # v1.0.0 (stable)
      hello-v1.1.0.yaml   # v1.1.0 (canary candidate)
```

Both files have `metadata.name: hello` but different `metadata.version`
values. The resolver discovers both and registers them under qualified
names: `hello` (primary) and `hello@1.1.0` (version-qualified).

### Canary configuration

Canary routing is configured in `workspace.yaml` under `server.canary`:

```yaml
server:
  canary:
    routes:
      - topology: hello
        versions:
          - version: "1.0.0"
            weight: 90
          - version: "1.1.0"
            weight: 10
            promote_when:
              min_runs: 50
              error_rate_below: 0.05
              drift_below: 0.30
              window_minutes: 60
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `topology` | string | Topology name (matches `metadata.name`) |
| `versions[].version` | semver | Topology version |
| `versions[].weight` | int (0–100) | Traffic percentage. Must sum to 100 across all versions |
| `versions[].promote_when` | object | Auto-promotion criteria (optional) |
| `promote_when.min_runs` | int | Minimum runs before promotion eligible |
| `promote_when.error_rate_below` | float | Max error rate (0.0–1.0) |
| `promote_when.drift_below` | float | Max average drift score |
| `promote_when.window_minutes` | int | Evaluation window for metrics |

### Routing algorithm

```python
import random

def select_version(route: CanaryRoute) -> str:
    """Weighted random selection."""
    roll = random.randint(1, 100)
    cumulative = 0
    for v in route.versions:
        cumulative += v.weight
        if roll <= cumulative:
            return v.version
    return route.versions[-1].version  # fallback
```

Simple weighted random. No session affinity (each request is independent).
Deterministic routing (e.g., by client_id hash) is a future enhancement.

### Version-qualified topology names

The resolver registers versioned topologies with qualified names:

- `hello` → resolves to the **default** version (highest weight, or stable)
- `hello@1.0.0` → resolves to exactly v1.0.0
- `hello@1.1.0` → resolves to exactly v1.1.0

Direct version access via `POST /run/hello@1.1.0` bypasses canary routing
for testing. The canary router only intercepts unqualified names that have
a canary route configured.

### Metrics tracking

Each version tracks independently:

```python
@dataclass
class CanaryMetrics:
    version: str
    total_runs: int = 0
    failed_runs: int = 0
    drift_scores: list[float] = field(default_factory=list)
    window_start: datetime = field(default_factory=lambda: datetime.now(UTC))
```

Metrics are emitted to OTel with version labels:

```python
record_run_started(topology_id="hello", version="1.1.0")
record_run_completed(topology_id="hello", version="1.1.0", duration_ms=...)
```

The `swarmkit.canary.version` attribute is added to all spans for
canary-routed runs.

### Auto-promotion

When all `promote_when` criteria are met within the evaluation window:

1. Canary version weight increases to 100
2. Previous stable version weight drops to 0
3. A `canary.promoted` audit event is recorded
4. Log message: `Canary promoted: hello 1.0.0 → 1.1.0`

Promotion is **logged but not persisted to workspace.yaml** — the operator
updates the config file manually (or via CI) after observing promotion.
This keeps the runtime read-only with respect to workspace files.

### Manual controls

```bash
# Check canary status
swarmkit canary status

# Override: send all traffic to canary
swarmkit canary promote hello --version 1.1.0

# Override: roll back to stable
swarmkit canary rollback hello

# Pin a specific version for testing
curl -X POST http://localhost:8000/run/hello@1.1.0 \
  -d '{"input": "test with canary version"}'
```

### Server integration

The canary router sits between the HTTP endpoint and job creation:

```
POST /run/hello
  → auth middleware
  → canary router (select version → hello@1.1.0)
  → job creation (topology=hello, version=1.1.0)
  → execute_job (runs hello@1.1.0)
  → metrics recorded with version tag
```

### What this does NOT do

- **No blue-green deployments.** This is weighted splitting, not environment switching.
- **No A/B testing with user segmentation.** Every request is independently routed by weight.
- **No automatic workspace.yaml modification.** Promotion is runtime-only; file changes are manual.
- **No multi-workspace canary.** Routing is within a single workspace's topology versions.

## Schema changes

### workspace.schema.json — server_config

Add `canary` to the `server_config` definition:

```json
"canary": {
  "type": "object",
  "properties": {
    "routes": {
      "type": "array",
      "items": { "$ref": "#/$defs/canary_route" }
    }
  }
}
```

### New $def: canary_route

```json
"canary_route": {
  "type": "object",
  "required": ["topology", "versions"],
  "properties": {
    "topology": { "type": "string" },
    "versions": {
      "type": "array",
      "minItems": 2,
      "items": { "$ref": "#/$defs/canary_version" }
    }
  }
}
```

### New $def: canary_version

```json
"canary_version": {
  "type": "object",
  "required": ["version", "weight"],
  "properties": {
    "version": { "$ref": "#/$defs/semver" },
    "weight": { "type": "integer", "minimum": 0, "maximum": 100 },
    "promote_when": { "$ref": "#/$defs/promote_criteria" }
  }
}
```

## Implementation plan

1. **Schema + Pydantic + TS codegen** — add canary types to workspace schema
2. **CanaryRouter** — weighted selection, metrics tracking, auto-promotion
3. **Server integration** — wire router into run/webhook endpoints
4. **CLI commands** — `swarmkit canary status/promote/rollback`
5. **Resolver update** — discover and register versioned topologies
6. **OTel attributes** — version tags on canary-routed spans
7. **Tests** — routing weights, promotion criteria, rollback, metrics
8. **Documentation** — usage guide with examples

## Open questions

1. **Session affinity:** should the same client_id always get the same
   version? Current design: no (stateless). Future: optional hash-based
   routing.
2. **Gradual ramp:** should promotion be instant (0→100) or gradual
   (10→25→50→100)? Current: instant. Gradual needs a ramp schedule.
