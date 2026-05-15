# SwarmKit v1.2: Structured delegation, crash-resilient multi-agent runs, and a real-world Sterling OMS workspace

**TL;DR:** SwarmKit is an open-source framework where multi-agent swarms are defined in YAML, not code. v1.2 introduces a planner-driven task execution model that fixes the biggest pain point in long-running agent systems: losing hours of work when something crashes.

[GitHub](https://github.com/delivstat/swarmkit) | [Design Doc](https://github.com/delivstat/swarmkit/blob/main/design/SwarmKit-Design-v0.6.md)

---

## What changed since v1.0

We shipped **192 PRs** from v1.0.0 to v1.2.8 in about 3 weeks. Here's what matters:

### Structured Delegation (the big one)

The old model: coordinators called `delegate_to_jira-researcher`, `delegate_to_config-analyst` etc. one by one. Models ignored instructions ("don't re-delegate"), created infinite loops, and if the run crashed after 4 agents finished, everything was lost.

The new model:

```
Architect calls create-task-plan → defines tasks with dependencies
Compiler executes tasks in parallel (independent) or serial (deps)
After each batch → architect reviews findings, can modify the plan
Self-tasks → architect does its own work (synthesis, diagrams)
All results persisted to disk → crash recovery built in
```

What this means in practice:
- **No more re-delegation loops.** The model creates a plan once; the compiler executes it.
- **Crash resilient.** Each completed task's results are saved to `.swarmkit/run-state/`. If the run crashes, your next `swarmkit run` detects the previous plan and asks "Resume from previous plan? [Y/n]"
- **Summary-first context.** The coordinator sees 3-5 bullet findings per task (~500 bytes), not 50KB of raw text. Full results available on-demand via `read-task-result`.
- **Auto-fixes.** Model forgot to add dependencies? Compiler auto-adds them. No synthesis task? Compiler injects one.

### Real-World Testing: Sterling OMS Workspace

We didn't just build the framework — we tested it on a production Sterling OMS implementation with real Jira tickets, Confluence pages, CDT configurations, 117K-line log files, and custom Java extensions.

The workspace has:
- **6 topologies** (solution review, QA, code review, coding assistant, sterling-assistant, document analysis)
- **11 archetypes** with per-agent model selection
- **70+ skills** across 18 MCP servers
- **Sub-agent architecture:** root → architect (Kimi K2.5) → 6 focused workers

Workers use different models based on their job:
| Agent | Model | Cost |
|-------|-------|------|
| Architect + Developer | Kimi K2.5 | $0.40/$1.90 per M |
| Jira/Config/Docs workers | DeepSeek V4 Flash | $0.14/$0.28 per M |
| Document writer | DeepSeek Chat V3 | $0.32/$0.89 per M |

### Sterling Log Analyser (MCP Server)

Built a dedicated MCP server that parses Sterling log4j FLAT logs into a SQLite index. Handles 500MB+ production logs. 9 analysis tools including `get-timer-detail` that drills into a slow API call and shows exactly WHY it was slow — every DB query, XML payload, and sub-call between the Begin and End markers.

The before/after was dramatic:
- **Without MCP (raw file reading):** 305-line grounded analysis
- **With MCP v1 (broken regex capturing 0.6% of timer data):** 36-line fabricated report with placeholder values
- **With MCP v2 (fixed regex, 99.95% coverage):** 200+ line grounded analysis matching the raw file quality

### Atlassian Wrapper MCP

Models consistently generated invalid JQL (`return window` as bare text, `ORDER BY` inside quotes, `OR` without field operators). Instead of teaching every model JQL syntax, we built a wrapper:

```json
// Model calls this (structured input):
{"keywords": ["return", "RETN", "RITN"], "project": "CROMA"}

// Wrapper generates valid JQL:
project = "CROMA" AND (text ~ "return" OR text ~ "RETN" OR text ~ "RITN") ORDER BY created DESC
```

Zero JQL parse errors since deployment.

### 18 Compiler Versions (v1.1.0 → v1.2.8)

Every version fixed a real production issue:
- **v1.1.0-v1.1.5:** Sequential delegation, forced synthesis, self-delegation detection
- **v1.1.6-v1.1.10:** MCP timeout/retry, coordinator nudge fixes, dynamic recursion limit
- **v1.1.11-v1.1.18:** Re-delegation loop fix, progress output, parallel delegation, routing fix, checkpoints, delegation caps
- **v1.2.0-v1.2.8:** Structured delegation, compiler split (1854 → 8 files), task execution engine, auto-fixes, trace hierarchy

### Other Notable Features

- **Progress output by default** — see exactly what every agent is doing in real-time
- **`swarmkit trace`** — call graph with token counts per agent and model
- **`swarmkit checkpoints`** — list resumable runs
- **`swarmkit why`** — LLM-powered analysis of what happened in a run
- **Document writer** — pandoc MCP for DOCX/PDF generation with style templates
- **Multimodal** — image content blocks through all 7 model providers
- **Intent drift detection** — detects when agents wander from the original goal

## What's Next

- M9 remaining: Code Review Swarm, Skill Authoring Swarm (make the reference topologies runnable e2e)
- M10: `swarmkit eject` (export to standalone LangGraph code) + `swarmkit serve` (HTTP server)
- M11: `pip install swarmkit` → working swarm in under 15 minutes

## Try It

```bash
pip install swarmkit-runtime
swarmkit init
swarmkit run . hello --input "Hello world"
```

Or check out the [Sterling OMS workspace](https://github.com/delivstat/swarmkit/tree/main/examples/sterling-oms) for a real-world example of a multi-agent system analysing enterprise software.

---

*SwarmKit is built by [delivstat](https://github.com/delivstat). Topology is data. Skills are the only extension primitive. Swarms grow through human-approved authoring.*
