# SDLC pipeline example — reusable archetype + skill library

Slice 2 of the SDLC pipeline example (design/details/sdlc-pipeline-example.md): the reusable
**archetype + skill library** the pipeline is composed from. The workspace, topologies, stage-graph,
KBs, and mock MCP servers come in later slices.

## Archetypes (`workspace/archetypes/`)

| Archetype | Role | Executor | Purpose |
| --- | --- | --- | --- |
| `release-orchestrator` | root | model | Owns a requirement's pipeline state; requests approval gates |
| `business-analyst` | leader | model | Intake + impact analysis → affected apps |
| `solution-architect` | worker | model | Per-app first-draft design |
| `integration-architect` | worker | model | Consolidated design + integration contracts |
| `developer` | worker | **harness** | Implements the design → candidate diff |
| `architect-reviewer` | worker | **harness** (read-only) | Investigative design↔code review |
| `security-consultant` | worker | **harness** (read-only) | Compliance / SAST / DAST review |
| `qa-engineer` | worker | model | Test plan + cases |
| `sit-qa` | worker | model | e2e cross-app testing |
| `pt-engineer` | worker | model | Performance testing + analysis |
| `release-coordinator` | leader | model | Deployment package + release notes |
| `support-engineer` | worker | model | Runbook / handover / prod monitoring |

## Skills (`workspace/skills/`)

`impact-analysis` (decision) · `consolidated-design-synthesis` (coordination) · `defect-triage`
(decision) · `test-plan-generation` (capability) · `code-review` (decision) · `pt-analysis`
(decision) · `artifact-judge` (decision) · `multi-party-approval-request` (coordination).

## Model configuration (env, two tiers)

Archetype models are **not hardcoded** — they reference env vars with defaults (resolved by the
runtime artifact env-substitution feature, design/details/artifact-env-substitution.md):

| Env var | Default | Used by |
| --- | --- | --- |
| `SDLC_REASONING_PROVIDER` / `SDLC_REASONING_MODEL` | `openrouter` / `moonshotai/kimi-k2.5` | orchestrator, qa, sit-qa, pt |
| `SDLC_WRITING_PROVIDER` / `SDLC_WRITING_MODEL` | `openrouter` / `deepseek/deepseek-v3` | analyst, architects, coordinator, support |

Harness archetypes (`developer`, `architect-reviewer`, `security-consultant`) use the `claude-code`
adapter, not a model.

## Validate

```
uv run python examples/sdlc-pipeline/validate_library.py
```
