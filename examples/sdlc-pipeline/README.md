# SDLC pipeline example

The SDLC pipeline example (design/details/sdlc-pipeline-example.md). Slice 2 shipped the reusable
**archetype + skill library**; slice 4 adds the **one-app (OMS) bounded stage run** — a workspace,
a role registry, a design funnel, and the intake→design topology. The multi-app synthesis,
stage-graph controller, harness build, and KBs come in later slices.

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

## Funnels (`workspace/funnels/`)

`consolidated-design-approval` — a first-class `kind: Funnel` artifact (the pipeline's first
consumer of the gate funnel, design/details/gate-funnel.md). It chains all four layers on the
consolidated-design artifact: deterministic `validate` → `judge` (`artifact-judge`) →
`review` (`architect-reviewer`, read-only) → multi-party `approve`. Referenced by id from a
topology node's `funnel:` field. See the [funnel reference](../../docs/site/reference/funnel.md).

## Model configuration (env, two tiers)

Archetype models are **not hardcoded** — they reference env vars with defaults (resolved by the
runtime artifact env-substitution feature, design/details/artifact-env-substitution.md):

| Env var | Default | Used by |
| --- | --- | --- |
| `SDLC_REASONING_PROVIDER` / `SDLC_REASONING_MODEL` | `openrouter` / `moonshotai/kimi-k2.5` | orchestrator, qa, sit-qa, pt |
| `SDLC_WRITING_PROVIDER` / `SDLC_WRITING_MODEL` | `openrouter` / `deepseek/deepseek-v3` | analyst, architects, coordinator, support |

Harness archetypes (`developer`, `architect-reviewer`, `security-consultant`) use the `claude-code`
adapter, not a model.

## The OMS stage run (slice 4)

One requirement flows through a bounded, deterministic stage sequence — the
agent-determination-only shape (code sequences the stages; agents only produce artifacts
and verdicts):

- `roles/sdlc-roles.yaml` — the role registry (oms-lead / web-lead / infosec-lead → identities).
- `funnels/oms-design-gate.yaml` — the OMS design gate: `judge` (`artifact-judge`) → multi-party
  `approve` (both leads, `min_distinct_approvers: 2`).
- `topologies/oms-stage-run.yaml` — `coordinator → intake (business-analyst) → designer
  (solution-architect, `funnel: oms-design-gate`)`.

The `StageRunner` runs the stages; the design stage blocks on its funnel (judge → real
multi-party approval, retry re-runs the architect). IAM scopes are per app, so an OMS agent
cannot reach a Web resource.

```
just demo-sdlc      # intake → design → judge → approval, a bounded retry, and an IAM-scope denial
```

## Validate

```
uv run python examples/sdlc-pipeline/validate_library.py
```
