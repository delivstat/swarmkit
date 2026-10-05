# swarmkit author benchmark

Measures what `swarmkit author {init,topology,skill,archetype,mcp-server}` actually produces across a corpus of real-world prompts. **Minimum-signal** scope (#1041 (1)): one base model (Kimi K2 via OpenRouter), eye-check + `swarmkit validate`, no LLM judge.

## Running

```bash
export OPENROUTER_API_KEY=sk-or-...
export SWARMKIT_AUTHOR_MODEL=openrouter/moonshotai/kimi-k2-0905  # defaults to this if unset

uv run python packages/runtime/benches/author/run.py
# → packages/runtime/benches/author/results/report.md
# → packages/runtime/benches/author/results/<prompt-id>/{workspace/, transcript.log, meta.json}
```

Use `--only topology,skill` to narrow modes, `--filter cold-brief` to run a single prompt.

## Prompt shape

Each prompt is a YAML file under `prompts/<mode>/<id>.yaml`:

```yaml
mode: topology                        # topology | skill | archetype | mcp-server | init
id: cold-brief
description: Starter morning-brief topology, bare workspace.
requirement: |
  Build a daily brief topology. One worker that pulls three items from an inbox
  and ranks them. Return a short JSON brief.
# Optional: seed the workspace with existing artifacts before running the author.
workspace_fixture:
  files:
    archetypes/greeter.yaml: |
      apiVersion: swarmkit/v1
      ...
# Optional: scripted replies the harness feeds after the requirement line.
# Defaults to ["yes", "yes", "yes"] — enough to approve typical plans + exits.
replies: [yes, yes, yes]
expect:
  must_validate: true                 # swarmkit validate on the resulting workspace
  files_created_min: 1                # at least N files appeared under topologies/, skills/, …
  wall_time_max_s: 180                # SLO; informational, does not fail the run
```

## What this measures

- **Does it produce a file at all?** (`generated` boolean)
- **Does the file validate against the canonical schema?** (`validates` boolean — the single most important signal)
- **How long?** (p50 / p95 wall time across runs)
- **How much?** (approximate token cost — pulled from the model provider's usage telemetry when available)
- **What did it look like?** Raw transcript + generated workspace preserved under `results/<prompt-id>/` for human review

## What this does **not** measure (yet)

- Semantic quality beyond schema validity (deferred to an LLM judge in (2))
- Multiple base models (deferred)
- `--thorough` mode's multi-agent swarm (deferred)
- Golden-artifact diffing (deferred)

## Interpreting the report

The matrix at the top of `results/report.md` shows pass/fail per prompt. If a mode's pass rate is low, the authoring prompts for that mode (`packages/runtime/src/swarmkit_runtime/authoring/_prompts.py`) need work — fix those before investing in richer evaluation.
