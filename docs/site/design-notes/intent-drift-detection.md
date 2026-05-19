---
title: Intent drift detection
description: Optional per-agent intent monitoring that detects semantic drift from the original goal during execution.
tags: [runtime, governance, observability]
status: draft
---

# Intent drift detection

**Scope:** runtime, schema (topology extension)
**Design reference:** §8 (governance / judicial), §14 (runtime architecture)
**Status:** draft

## Goal

Detect when an agent's outputs drift semantically from the original intent during multi-step execution, and optionally nudge the agent back on track.

## Background

A common failure mode in multi-step agent pipelines: by step 4-5 the agent is solving a slightly different problem than what was originally given. Not hallucination, not a model issue — the intent quietly decays at every handoff. This is especially pronounced in multi-agent topologies where context passes through several agents.

Prior art: [State Integrity Protocol](https://github.com/sijan324/state-integrity-protocol) — computes `1 - cosine_similarity(anchor_embedding, step_output_embedding)` per step against a fixed threshold. Simple and effective as a diagnostic, but static thresholds and no learning.

## Non-goals

- Replacing governance policy checks (§8) — this is observability, not authorization
- Hallucination detection — drift and hallucination are different failure modes
- Enforced by default — this is opt-in per topology or per agent

## Design

### Topology schema extension

Intent monitoring is an optional field on agents and/or at topology level. It follows the same pattern as `depends_on` — present when needed, ignored when absent.

**Per-agent:**

```yaml
agents:
  - id: researcher
    archetype: deep-researcher
    intent_monitoring:
      enabled: true
      threshold: 0.25          # explicit, or "auto" (see open questions)
      on_drift: nudge           # nudge | warn | log
```

**Topology-level default:**

```yaml
topology:
  id: my-swarm
  intent_monitoring:
    enabled: true
    default_strategy: nudge
    default_threshold: 0.25
```

Per-agent settings override topology-level defaults. Agents can disable monitoring even when the topology enables it.

### Drift strategies

| Strategy | Behavior |
|----------|----------|
| `log` | Record drift score in audit log, no intervention |
| `warn` | Log + emit a warning event the UI/CLI can surface |
| `nudge` | Inject a system message reminding the agent of the original goal |

There is no `block` strategy in v1. Blocking execution based on embedding similarity is too blunt without learned thresholds — false positives would degrade the user experience.

### Core algorithm

1. **Anchor:** embed the agent's assigned goal (from topology `goal` field or the originating user query) as the reference vector.
2. **Observe:** after each agent step, embed the output and compute drift: `drift = 1 - cosine_similarity(anchor, output)`.
3. **Act:** if drift exceeds the threshold, execute the configured strategy.

Embedding backend: sentence-transformers by default (local, no API keys). Must go through the `ModelProvider` interface if using an API-based embedding model.

### Separation from tool errors

Tool errors (API failures, timeouts, malformed responses) must not be scored as intent drift. The observer only scores `agent_reasoning` events from the audit log. `tool_error` and `tool_response` events are excluded from drift calculation but logged separately for diagnostics.

### Audit integration

Drift scores are recorded as structured fields in the existing audit log (§8.3):

```json
{
  "event": "agent_step",
  "agent_id": "researcher",
  "step": 4,
  "intent_drift": {
    "score": 0.31,
    "threshold": 0.25,
    "action_taken": "nudge"
  }
}
```

## Self-learning (`threshold: auto`)

> **Status: needs more clarity.** The ideas below are directional. The learning mechanism, feedback signals, and storage format need further design before `auto` can ship.

The static threshold problem: a `deep-researcher` agent naturally diverges more than a `validator` — that's its job. A fixed 0.25 threshold will over-trigger on exploratory agents and under-trigger on focused ones.

### Concept

Persist drift profiles across runs per topology. After enough runs, `threshold: auto` derives a learned boundary between productive divergence and harmful drift.

Possible storage — a sidecar JSON file per topology:

```json
{
  "topology": "my-swarm",
  "runs": 47,
  "agents": {
    "researcher": {
      "mean_drift": 0.23,
      "std_drift": 0.08,
      "drift_at_failure": [0.41, 0.38, 0.45],
      "learned_threshold": 0.35
    }
  }
}
```

### Unsolved questions for auto mode

1. **Feedback signal.** The system needs to know which runs were "good" and which were "bad" to learn meaningful thresholds. Options:
   - Explicit user rating (thumbs up/down)
   - Implicit signals (did the user re-run? did they edit the output?)
   - Structural signals (did downstream validation skills pass?)
   - Some combination of these
2. **Cold start.** How many runs before `auto` is useful? What does the system do before it has enough data — fall back to a conservative default?
3. **Drift across topology versions.** If the user modifies the topology, do learned profiles reset or carry over?
4. **Per-archetype priors.** Should archetypes ship with baseline drift expectations (e.g., researchers: high tolerance, validators: low tolerance) to reduce cold-start pain?
5. **Storage location.** Sidecar files alongside topology YAML? A workspace-level store? A SQLite database?

`auto` mode should not ship until these questions have answers. Initial implementation should support explicit numeric thresholds only.

## API shape

```python
@dataclass
class IntentMonitoringConfig:
    enabled: bool = False
    threshold: float = 0.25
    on_drift: Literal["log", "warn", "nudge"] = "log"

class IntentObserver:
    def set_anchor(self, goal: str) -> None: ...
    def observe(self, step: int, output: str) -> DriftResult: ...

@dataclass
class DriftResult:
    score: float
    threshold: float
    exceeded: bool
    action_taken: str | None
```

## Test plan

- Unit: drift calculation with known embeddings, threshold triggering, strategy dispatch
- Unit: tool error events excluded from drift scoring
- Integration: end-to-end topology run with intent monitoring enabled, verify audit log entries
- Test data: synthetic agent traces with controlled drift patterns (low-drift, gradual-drift, sudden-drift)

## Demo plan

A reference topology under `examples/` with intent monitoring enabled. Run it, show the CLI output with drift scores per step, demonstrate a nudge firing when drift exceeds threshold.

## Open questions

- Should the nudge message be customizable in the topology YAML, or is a generic "refocus on your original goal" sufficient?
- How does this interact with DAG topologies where agents have different sub-goals? Per-agent anchoring handles this, but should there also be a topology-level "north star" anchor?
- Is sentence-transformers the right default, or should we start with TF-IDF (zero dependencies) and upgrade later?
