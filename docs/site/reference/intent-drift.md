# Intent drift detection

Detects when agents wander from the original goal during multi-step execution. Optional, per-topology or per-agent.

## Quick start

Add to your topology YAML:

```yaml
intent_monitoring:
  enabled: true
  threshold: 0.75
  on_drift: nudge   # log | warn | nudge
```

Run with verbose to see drift scores:

```bash
swarmkit run my-swarm/ my-topology --input "Review code for security" --verbose
```

Output:

```
  [drift] score=0.41 threshold=0.75     (on-topic response — OK)
  [drift] score=0.97 threshold=0.75 → nudge injected   (off-topic — corrected)
```

## How it works

1. **Anchor:** The user's original input is embedded as the reference vector
2. **Observe:** After each agent step, the output is embedded and compared
3. **Score:** `drift = 1 - cosine_similarity(anchor, output)`
4. **Act:** If drift exceeds the threshold, the configured strategy fires

## Configuration

### Topology-level (default for all agents)

```yaml
intent_monitoring:
  enabled: true
  threshold: 0.75
  on_drift: log
```

### Per-agent override

```yaml
agents:
  root:
    id: root
    role: root
    children:
      - id: researcher
        role: worker
        archetype: deep-researcher
        intent_monitoring:
          enabled: true
          threshold: 0.9    # researchers explore — higher tolerance
          on_drift: log
      - id: validator
        role: worker
        archetype: validator
        intent_monitoring:
          enabled: true
          threshold: 0.5    # validators should stay focused
          on_drift: nudge
```

## Strategies

| Strategy | What happens |
|---|---|
| `log` | Drift score recorded in audit log. No intervention. |
| `warn` | Same as log. Visible in `swarmkit logs` and OTel events. |
| `nudge` | Injects a system message: "You are drifting from your original goal. Refocus on: [goal]" |

## Threshold guide

Tested with sentence-transformers (all-MiniLM-L6-v2):

| Range | Sensitivity | Use case |
|---|---|---|
| 0.4-0.5 | Very aggressive | Triggers on any rephrasing. Only for highly constrained tasks. |
| 0.6-0.7 | Moderate | Triggers when topic shifts noticeably. Good for focused workers. |
| **0.75** | **Default** | Triggers on clearly unrelated content. Safe for most use cases. |
| 0.9+ | Permissive | Only triggers on completely random output. Good for exploratory agents. |

## Embedding backend

- **sentence-transformers** (recommended): Semantic similarity via `all-MiniLM-L6-v2` (ONNX). Downloads ~80MB on first use, cached in `~/.cache/huggingface/`.
- **TF-IDF fallback**: Used automatically when sentence-transformers is not installed. Token-based matching — less accurate but zero dependencies.

Install for best results:

```bash
pip install sentence-transformers
```

## Audit integration

Every drift observation is recorded as an audit event:

```json
{
  "event_type": "intent.drift",
  "agent_id": "researcher",
  "payload": {
    "drift_score": 0.41,
    "threshold": 0.75,
    "exceeded": false,
    "action": null
  }
}
```

Visible via `swarmkit logs` and queryable from the AuditProvider.

## OTel integration

Drift events are emitted as OTel span events with `swarmkit.drift.*` attributes:

| Attribute | Type | Description |
|---|---|---|
| `swarmkit.drift.score` | float | 0.0 (aligned) to 1.0+ (fully drifted) |
| `swarmkit.drift.threshold` | float | Configured threshold |
| `swarmkit.drift.action` | string | log / warn / nudge |
| `swarmkit.drift.exceeded` | bool | Whether threshold was breached |

## What drift detection does NOT do

- **Hallucination detection** — drift measures topic relevance, not factual accuracy
- **Block execution** — no `block` strategy in v1 (too many false positives)
- **Self-learning** — `threshold: auto` is planned but not yet implemented (needs run history)
- **Replace governance** — drift is observability, not authorization. Governance (IAM scopes, policy engine) handles permissions.
