# Level 12: Triggers & Canary Deployments

Schedule automatic runs and safely roll out topology changes with traffic splitting.

## What you'll learn

- Cron triggers (scheduled execution)
- Webhook triggers (event-driven execution)
- Canary deployments (gradual rollout)
- Auto-promotion by metrics
- Rollback

## Triggers

Triggers automatically fire topology runs on schedule or in response to events.

### 1. Cron trigger

```bash
mkdir triggers
```

```yaml
# triggers/nightly-review.yaml
apiVersion: swarmkit/v1
kind: Trigger
metadata:
  id: nightly-review
  name: Nightly Code Review
type: cron
schedule: "0 2 * * *"       # 2 AM daily
topology: content-team
input: "Review all PRs opened today"
enabled: true
```

The trigger scheduler runs inside `swarmkit serve` — start the server and the trigger fires automatically.

### 2. Webhook trigger

```yaml
# triggers/pr-webhook.yaml
apiVersion: swarmkit/v1
kind: Trigger
metadata:
  id: pr-webhook
  name: PR Webhook
type: webhook
topology: content-team
enabled: true
auth:
  method: hmac
  secret: "${WEBHOOK_SECRET}"    # HMAC-SHA256 signature validation
```

When a webhook arrives at `/webhooks/pr-webhook`, the server validates the HMAC signature and fires the topology:

```bash
# Send a webhook (from GitHub, CI, etc.)
curl -X POST http://localhost:8000/webhooks/pr-webhook \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=..." \
  -d '{"action": "opened", "pull_request": {"number": 42}}'
```

### 3. Webhook auth methods

| Method | How it works |
|--------|-------------|
| `hmac` | Validates `X-Hub-Signature-256` header (GitHub-compatible) |
| `bearer` | Checks `Authorization: Bearer <token>` header |
| `api_key` | Checks a custom header for a static key |

### 4. List triggers

```bash
curl http://localhost:8000/triggers
```

## Canary deployments

Roll out topology changes gradually — split traffic between versions, monitor metrics, auto-promote when safe.

### 5. Configure canary routing

```yaml
# workspace.yaml — add canary config
server:
  canary:
    routes:
      - topology: content-team
        versions:
          - version: "1.0.0"
            weight: 90           # 90% of traffic
          - version: "1.1.0"
            weight: 10           # 10% of traffic (canary)
            promote_when:
              min_runs: 50       # need 50 runs before promoting
              error_rate_below: 0.05   # less than 5% errors
              drift_below: 0.30        # drift score under 0.30
              window_minutes: 60       # measure over last hour
```

### 6. How it works

When a request comes in for `content-team`:
1. The canary router picks version `1.0.0` (90%) or `1.1.0` (10%) based on weights
2. The selected version's topology runs
3. Metrics are recorded: success/failure, drift score, duration
4. After 50 runs of `1.1.0` with <5% error rate and <0.30 drift, it auto-promotes

### 7. Monitor canary status

```bash
# View canary routes and metrics
curl http://localhost:8000/canary

# Response:
# {
#   "routes": [{
#     "topology": "content-team",
#     "versions": [
#       {"version": "1.0.0", "weight": 90, "metrics": {"total_runs": 450, "error_rate": 0.02}},
#       {"version": "1.1.0", "weight": 10, "metrics": {"total_runs": 48, "error_rate": 0.04}}
#     ]
#   }]
# }
```

### 8. Manual promote/rollback

```bash
# Promote canary to 100%
curl -X POST http://localhost:8000/canary/content-team/promote

# Rollback — remove canary, revert to stable
curl -X POST http://localhost:8000/canary/content-team/rollback
```

### 9. Topology versioning

Create versioned topology files:

```yaml
# topologies/content-team.yaml — version 1.0.0
metadata:
  id: content-team
  version: "1.0.0"

# topologies/content-team-v1.1.yaml — version 1.1.0
metadata:
  id: content-team
  version: "1.1.0"
  # Changes: added security reviewer
```

## Your workspace so far

```
my-swarm/
├── workspace.yaml          # canary routes, server config
├── triggers/
│   ├── nightly-review.yaml
│   └── pr-webhook.yaml
└── topologies/
    ├── content-team.yaml        # v1.0.0
    └── content-team-v1.1.yaml   # v1.1.0 (canary)
```

## Next

[Level 13: Authoring & Review](13-authoring-review.md) — create artifacts through conversation, not by writing YAML.
