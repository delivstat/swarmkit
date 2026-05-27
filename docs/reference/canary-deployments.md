# Canary Deployments

Gradually roll out topology changes by splitting traffic between versions.
Monitor error rates and drift scores; promote automatically when criteria
are met, or promote/rollback manually.

## Quick start

### 1. Create two versions of your topology

Place both in your workspace's `topologies/` directory:

```
workspace/
  topologies/
    my-swarm.yaml          # v1.0.0 (stable)
    my-swarm-v1.1.0.yaml   # v1.1.0 (canary)
```

Both files have `metadata.name: my-swarm` but different versions:

```yaml
# my-swarm.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: my-swarm
  version: "1.0.0"
  description: Production-stable version
agents:
  root:
    id: coordinator
    role: root
    archetype: my-coordinator
    children:
      - id: worker
        role: worker
        archetype: my-worker
```

```yaml
# my-swarm-v1.1.0.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: my-swarm
  version: "1.1.0"
  description: Canary — new model, updated prompts
agents:
  root:
    id: coordinator
    role: root
    archetype: my-coordinator-v2    # updated archetype
    children:
      - id: worker
        role: worker
        archetype: my-worker-v2     # updated archetype
```

### 2. Configure canary routing in workspace.yaml

```yaml
server:
  canary:
    routes:
      - topology: my-swarm
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

### 3. Start the server

```bash
swarmkit serve workspace/ --port 8000
```

The canary router initializes at startup:

```
INFO  CanaryRouter initialized with 1 route(s): my-swarm (2 versions)
```

### 4. Send requests normally

```bash
curl -X POST http://localhost:8000/run/my-swarm \
  -H "Content-Type: application/json" \
  -d '{"input": "Process this request"}'
```

The server automatically routes ~90% of requests to v1.0.0 and ~10% to
v1.1.0. The `version` field in the job response shows which version
handled the request.

---

## Configuration reference

### workspace.yaml `server.canary` block

```yaml
server:
  canary:
    routes:
      - topology: <topology-name>
        versions:
          - version: "<semver>"
            weight: <0-100>
            promote_when:          # optional
              min_runs: <int>
              error_rate_below: <0.0-1.0>
              drift_below: <0.0-2.0>
              window_minutes: <int>
```

### Field reference

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `topology` | string | yes | — | Topology name (matches `metadata.name` in topology YAML) |
| `versions` | array | yes | — | At least 2 version entries. Weights must sum to 100 |
| `version` | semver | yes | — | Must match a topology file's `metadata.version` |
| `weight` | int | yes | — | Traffic percentage (0–100) |
| `promote_when` | object | no | — | Auto-promotion criteria. Omit to disable auto-promotion |
| `min_runs` | int | no | 50 | Minimum completed runs before promotion is eligible |
| `error_rate_below` | float | no | 0.05 | Max allowed error rate (failed/total). 0.05 = 5% |
| `drift_below` | float | no | 0.30 | Max average intent drift score |
| `window_minutes` | int | no | 60 | Metrics evaluation window. Older runs are discarded |

### Weight rules

- Weights across all versions in a route must sum to **100**
- A version with weight **0** receives no traffic
- A version with weight **100** receives all traffic
- The server logs a warning if weights don't sum to 100

---

## How routing works

Each incoming request to `POST /run/{topology}` or `POST /hooks/{topology}`
is intercepted by the canary router:

1. **Check for canary route** — if the topology has a canary route configured,
   the router selects a version using weighted random selection
2. **Resolve version** — the topology name becomes version-qualified
   (e.g., `my-swarm@1.1.0`) for the runtime to look up
3. **Execute** — the job runs against the selected version
4. **Track metrics** — success/failure and drift scores are recorded
5. **Check promotion** — if criteria are met, the canary is auto-promoted

### Direct version access

Bypass canary routing by using a version-qualified name:

```bash
# Always runs v1.1.0, regardless of canary weights
curl -X POST http://localhost:8000/run/my-swarm@1.1.0 \
  -H "Content-Type: application/json" \
  -d '{"input": "Test against canary directly"}'
```

---

## Monitoring canary status

### GET /canary

```bash
curl -s http://localhost:8000/canary | jq .
```

Response:

```json
{
  "enabled": true,
  "routes": [
    {
      "topology": "my-swarm",
      "versions": [
        {
          "version": "1.0.0",
          "weight": 90,
          "metrics": {
            "total_runs": 180,
            "failed_runs": 2,
            "error_rate": 0.0111,
            "avg_drift": 0.15,
            "window_start": "2026-05-27T10:00:00+00:00"
          }
        },
        {
          "version": "1.1.0",
          "weight": 10,
          "metrics": {
            "total_runs": 20,
            "failed_runs": 0,
            "error_rate": 0.0,
            "avg_drift": 0.12,
            "window_start": "2026-05-27T10:00:00+00:00"
          },
          "promote_when": {
            "min_runs": 50,
            "error_rate_below": 0.05,
            "drift_below": 0.30,
            "window_minutes": 60
          }
        }
      ]
    }
  ],
  "promotions": []
}
```

### Job listing with version

```bash
curl -s http://localhost:8000/jobs | jq .
```

```json
[
  {
    "job_id": "abc123",
    "topology": "my-swarm",
    "version": "1.1.0",
    "status": "completed",
    "created_at": "2026-05-27T12:00:00+00:00",
    "completed_at": "2026-05-27T12:00:05+00:00"
  }
]
```

---

## Auto-promotion

When **all** `promote_when` criteria are met simultaneously within the
evaluation window:

1. The canary version's weight is set to **100**
2. All other versions' weights are set to **0**
3. A `canary.promoted` event is logged
4. The promotion is recorded in the `/canary` status response

```
INFO  Canary promoted: my-swarm → v1.1.0 (runs=50, error_rate=0.020, avg_drift=0.150)
```

**Important:** Auto-promotion only changes runtime routing. It does **not**
modify workspace.yaml. After observing a successful promotion, update the
config file manually (or via CI) to make it permanent.

### Promotion criteria explained

| Criterion | What it checks | Example |
|-----------|---------------|---------|
| `min_runs: 50` | At least 50 completed runs on this version | Ensures statistical significance |
| `error_rate_below: 0.05` | Less than 5% of runs failed | Catches regressions |
| `drift_below: 0.30` | Average drift score under 0.30 | Ensures agents stay on-task |
| `window_minutes: 60` | Only count runs from the last 60 minutes | Focuses on recent performance |

All criteria must be satisfied **at the same time**. If any single criterion
fails, promotion does not happen.

---

## Manual controls

### Promote a version

```bash
curl -X POST http://localhost:8000/canary/my-swarm/promote \
  -H "Content-Type: application/json" \
  -d '{"version": "1.1.0"}'
```

Response:

```json
{"promoted": true, "topology": "my-swarm", "version": "1.1.0"}
```

This immediately sets the promoted version to 100% and all others to 0%.

### Roll back

```bash
curl -X POST http://localhost:8000/canary/my-swarm/rollback \
  -H "Content-Type: application/json"
```

Response:

```json
{"rolled_back": true, "topology": "my-swarm"}
```

Rollback sets the **first** version (typically the stable one) to 100%
and all others to 0%.

---

## Common scenarios

### Gradual rollout of a new model

```yaml
# Week 1: 5% canary
versions:
  - version: "1.0.0"
    weight: 95
  - version: "1.1.0"
    weight: 5
    promote_when:
      min_runs: 100
      error_rate_below: 0.02
      drift_below: 0.25
      window_minutes: 1440    # 24-hour window
```

### A/B test between model providers

```yaml
versions:
  - version: "2.0.0"         # Claude Opus
    weight: 50
  - version: "2.1.0"         # Kimi K2.5
    weight: 50
    # No promote_when — manual comparison
```

Monitor via `GET /canary` to compare error rates and drift scores,
then manually promote the winner.

### Quick rollback after deployment

```bash
# Deploy canary
# ...something goes wrong...

# Immediate rollback via API
curl -X POST http://localhost:8000/canary/my-swarm/rollback

# Or restart with updated workspace.yaml (set canary weight to 0)
```

---

## Integration with observability

Canary metrics integrate with SwarmKit's existing observability:

- **OTel spans** — canary-routed runs include version metadata
- **Intent drift** — drift scores feed into canary promotion criteria
- **Audit log** — promotion/rollback events are logged
- **Job listing** — version field visible in `GET /jobs` responses

---

## Limitations

- **No session affinity** — each request is independently routed by weight.
  The same client may hit different versions on consecutive requests.
- **Runtime-only promotion** — auto-promotion changes routing weights in
  memory but does not modify workspace.yaml. Restart resets to config.
- **Single workspace** — canary routing is within one workspace. No
  cross-workspace version routing.
- **No gradual ramp** — promotion is instant (0% → 100%). Gradual ramp
  schedules (10% → 25% → 50% → 100%) are a future enhancement.
