# SDLC pipeline example

The SDLC pipeline example (design/details/sdlc-pipeline-example.md). Slice 2 shipped the reusable
**archetype + skill library**; slice 4 adds the **one-app (OMS) bounded stage run** — a workspace,
a role registry, a design funnel, and the intake→design topology; slice 5 adds the **controller +
stage-graph**; slice 6 adds the **consolidated design across all three apps** (synthesis) with the
**architect-reviewer harness review** as layer 3 of the design funnel. The harness build and KBs
come in later slices.

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

Harness archetypes (`developer`, `architect-reviewer`, `security-consultant`) run a coding harness,
not a model — `executor: { kind: harness, ref: claude-code }` (design executor-abstraction.md §4.2).

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

## The consolidated design (slice 6)

The multi-app design stage: three per-app **solution architects** draft first-pass designs in
parallel, each **IAM-scoped to its own app** (`app:oms:read` / `app:web:read` / `app:mobile:read`,
so the teams stay walled), and the cross-cutting **integration architect** synthesises them into
**one consolidated design** (the `consolidated-design-synthesis` skill) that parks on the
four-layer `consolidated-design-approval` funnel:

- `topologies/consolidated-design.yaml` — `coordinator → {oms,web,mobile}-designer
  (solution-architect) → integration-designer (integration-architect, `funnel:
  consolidated-design-approval`)`. The integration architect reads across all three apps but
  writes only the shared design artifact.
- `funnels/consolidated-design-approval.yaml` — `validate` → `judge` (`artifact-judge`) →
  **`review`** (the `architect-reviewer` **harness**, read-only, layer 3 investigative review)
  → multi-party `approve` (oms-lead + web-lead + mobile-lead + infosec-lead). A harness finding at
  or above `route_back_at: high` routes back to a revision before any human is paged; lower
  findings attach and travel to the approvers.
- `roles/sdlc-roles.yaml` — now completes the app-lead set with **`mobile-lead`** (carol), so all
  four required parties resolve to distinct human identities.

The `architect-reviewer` is layer 3 of the gate funnel (design/details/gate-funnel.md,
harness-reviewer.md): unlike the text-only judge, the harness *investigates* — it opens the repo +
KBs and cross-checks the consolidated design against the actual code and integration contracts.

> Note: the integration designer runs *after* the three app drafts by children order + the
> StageRunner/demo sequencing, not a `depends_on` field — that child-agent key is declared in the
> topology schema but currently rejected by the base agent's `additionalProperties: false` (a
> JSON Schema `allOf` gotcha; a schema fix is out of scope for this example-only slice).

```
just demo-consolidated-design   # 3 app designs → consolidation → 4-layer funnel (incl. harness
                                # review) → 4-party approval, plus a route-back on a HIGH finding
```

## The pipeline controller (slice 5)

The pipeline as data + the saga that runs it. `pipelines/oms-pipeline.yaml` is a
`kind: StageGraph` — intake → design (gated + contract-locked) → build → sit, with a
defect loop. The **controller** (`controller/`) is a self-contained, runtime-free service
that sequences a requirement across those stages over an injectable `run_stage` seam:

- durable per-requirement saga state; events deduped on `(requirement_id, event, source_event_id)`;
- **reconciliation** recovers a dropped event by pulling source state;
- **per-contract locking** — all-or-none in fixed order; a contended requirement parks and resumes;
- **failure vs wait** — a park is free state; a failed run retries idempotently, then surfaces to a human;
- **cancellation** unwinds with each passed stage's `compensation` run in reverse order.

It drives SwarmKit only inside bounded stage runs (the slice-4 `StageRunner`) — the Minder
split: the app owns weeks-long logic + state, SwarmKit does bounded determination + governance.
Design: [`pipeline-controller.md`](../../design/details/pipeline-controller.md).

```
just demo-pipeline-controller   # one requirement through the pipeline + duplicate/dropped/contended/cancelled scenarios
```

## Orchestration: the pluggable sequencing seam

Pipeline *sequencing* is a **provider seam**, not a bespoke engine
([`orchestration-provider-seam.md`](../../design/details/orchestration-provider-seam.md)). SwarmKit
keeps the `StageGraph` spec, the governed stage runs, and the correlated audit; the durable
saga substrate is delegated. Two adapters implement `OrchestrationProvider`:

- **Reference controller** (`controller/`) — the zero-infra, in-memory option (slice 5).
- **Temporal** (`orchestrator/temporal/`) — the production adapter: a single data-driven Temporal
  workflow interprets any StageGraph (stages → activities that run governed SwarmKit stage runs;
  gate resolutions + external events → signals; compensation → the saga pattern). The graph stays
  data — one workflow runs any pipeline.

```
just demo-pipeline-temporal      # the OMS pipeline on Temporal (in-process test env, no server)
# `just` uses `uv run --group orchestrator`, which pulls temporalio in on demand. To run the
# tests directly, sync the group — but this is a *virtual* uv workspace, so you must keep the
# workspace members (fastapi, swarmkit_runtime) with `--all-packages`, else the sync prunes them:
uv sync --all-packages --group orchestrator     # installs temporalio (kept out of the core deps)
uv run --group orchestrator pytest packages/runtime/tests/test_orchestration_temporal.py -m integration
```

The Temporal tests run under the SDK's in-process time-skipping environment — no external server —
and are gated `integration` (deselected in default CI, which does not install temporalio).

## Validate

```
uv run python examples/sdlc-pipeline/validate_library.py
```
