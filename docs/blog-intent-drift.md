# Intent Drift Detection: How we keep multi-agent swarms on track

When you ask an AI agent "analyse this log file for performance issues," something interesting happens around step 10. The agent starts reporting on code formatting. By step 15, it's suggesting architectural improvements unrelated to the log. By step 20, it's writing a generic performance checklist copied from its training data.

Each step seems reasonable in isolation. But the agent has drifted far from what you asked for. We call this **intent drift** — the gradual semantic divergence from the original goal during multi-step execution.

## The problem with long-running agents

In a multi-agent swarm, drift compounds. A coordinator delegates to 5 workers. Each worker runs 20-50 tool calls. If any worker drifts, the coordinator synthesizes garbage. If the coordinator drifts, it delegates irrelevant tasks.

We saw this in production:
- A developer agent asked to "check return order code" started reading unrelated order creation code by turn 15
- A Jira researcher asked to "find tickets about RETN returns" expanded to searching all order types by turn 10
- An architect asked to "analyse log file slowness" created a plan including docs-researcher and config-analyst that weren't needed

The common pattern: the agent's context grows with each tool result, and the most recent results influence the next action more than the original goal. The original instruction is buried under 50KB of tool outputs.

## How intent drift detection works

The solution is surprisingly simple: **embed the original goal, embed each agent output, measure the cosine distance.**

```
                    Original Goal
                    "Analyse log file for slowness"
                         │
                         │ embed → anchor vector
                         │
Step 1 output ──embed──→ cosine_similarity(anchor, step1) = 0.85 → drift = 0.15 ✅
Step 5 output ──embed──→ cosine_similarity(anchor, step5) = 0.72 → drift = 0.28 ✅
Step 10 output ──embed──→ cosine_similarity(anchor, step10) = 0.40 → drift = 0.60 ⚠️
Step 15 output ──embed──→ cosine_similarity(anchor, step15) = 0.20 → drift = 0.80 ❌ NUDGE!
```

When the drift score exceeds the threshold, the system injects a nudge message into the agent's conversation:

> "You are drifting from your original goal. Refocus on: Analyse log file for performance issues. Do not introduce unrelated topics or tasks."

The agent reads this as a user message and course-corrects. In practice, one nudge is usually enough to get the agent back on track.

## The implementation

### Enabling it

Add `intent_monitoring` to any agent in your topology YAML:

```yaml
agents:
  root:
    id: code-reviewer
    role: worker
    archetype: code-reviewer
    intent_monitoring:
      enabled: true
      threshold: 0.75
      on_drift: nudge
```

Three strategies:
- **`log`** — record drift score in audit events, do nothing visible. Good for monitoring.
- **`warn`** — log + emit warning event. Good for dashboards and alerting.
- **`nudge`** — inject a refocus message into the agent's conversation. Good for production.

### Threshold tuning

The threshold is `1 - cosine_similarity`. Higher = more permissive.

| Threshold | Behaviour | Use case |
|-----------|-----------|----------|
| 0.4-0.5 | Very aggressive — triggers on rephrasing | Strict compliance tasks |
| 0.6-0.7 | Moderate — triggers on topic shift | Research agents with focused scope |
| 0.75 | Default — triggers on clearly unrelated content | General purpose |
| 0.9+ | Permissive — only triggers on random output | Creative/exploratory tasks |

For our enterprise workspace, we use:
- **0.75** for the architect (needs some latitude for planning)
- **0.70** for workers (should stay tight to their specific task)

### Runs entirely local

The embedding uses `sentence-transformers/all-MiniLM-L6-v2` with ONNX backend:
- **No API calls** — runs on your CPU, no OpenAI/Anthropic needed
- **~80MB model** — downloaded once, cached at `~/.cache/huggingface/`
- **~5ms per embedding** — negligible overhead per agent step
- **384-dimensional vectors** — good balance of accuracy and speed

If sentence-transformers isn't installed, it falls back to a TF-IDF hash-based embedder — less accurate but zero dependencies.

### Every observation is an audit event

```json
{
  "event_type": "intent.drift",
  "agent_id": "jira-researcher",
  "payload": {
    "drift_score": 0.82,
    "threshold": 0.70,
    "exceeded": true,
    "action": "nudge"
  }
}
```

You can query these to see drift patterns over time:
- Which agents drift most?
- At what step does drift typically happen?
- Does nudging actually fix the drift or does the agent drift again?

## What we learned

### 1. Workers drift more than coordinators

Workers have 10-25 tools and make 20-50 tool calls per run. By turn 15, the accumulated context is massive and the original instruction is far away. Coordinators make fewer calls and see structured summaries, so they stay on track.

**Fix:** Lower threshold (0.70) for workers, higher (0.75) for coordinators.

### 2. Tool results are the main drift vector

When a research agent searches for "return orders" and gets back results mentioning "exchange orders," the next search broadens to include exchanges. Then exchanges lead to cancellations. Each step follows logically from the previous result, but the chain drifts from the original goal.

**Fix:** The nudge message references the original goal, not the intermediate results. This breaks the drift chain.

### 3. One nudge is usually enough

In our testing, agents that received a nudge almost always course-corrected on the next step. The drift score dropped back below threshold and stayed there. We haven't seen cases where an agent needs repeated nudging.

### 4. Log before you nudge

Start with `on_drift: log` to understand your agents' drift patterns before enabling nudge. You might find that some agents naturally stay on track and don't need monitoring, while others consistently drift at specific steps.

## The bigger picture

Intent drift detection is one layer in a defense-in-depth approach:

1. **Topology design** — focused agents with scoped tools (prevention)
2. **Structured delegation** — planner-driven tasks with explicit instructions (prevention)
3. **Intent drift detection** — embedding-based drift monitoring with nudge (correction)
4. **Tool limit** — forced synthesis after N turns (hard stop)
5. **Delegation cap** — max re-delegations per child (hard stop)

Each layer catches what the previous one missed. The goal isn't to prevent all drift — it's to keep agents productive and on-topic for long-running tasks where context grows faster than the model can track.

## Try it

```yaml
# In your topology YAML, add to any agent:
intent_monitoring:
  enabled: true
  threshold: 0.75
  on_drift: nudge
```

Run with `--verbose` to see drift scores in real-time:
```
[drift] score=0.1823 threshold=0.75 → OK
[drift] score=0.4291 threshold=0.75 → OK
[drift] score=0.8134 threshold=0.75 → nudge injected
```

The full implementation is ~345 lines of Python. No external services, no API keys, no training data. Just cosine similarity on local embeddings.

---

*Intent drift detection shipped in SwarmKit M7. [GitHub](https://github.com/delivstat/swarmkit) | [Design note](https://github.com/delivstat/swarmkit/blob/main/design/details/intent-drift-detection.md)*
