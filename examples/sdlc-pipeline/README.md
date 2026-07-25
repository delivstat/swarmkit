# SDLC pipeline example

The SDLC pipeline example (design/details/sdlc-pipeline-example.md) — **now the complete SDLC
pipeline**. It models an end-to-end software-delivery lifecycle for a multi-app D2C retailer,
composed entirely as data in one SwarmKit workspace, with an agent doing the first-pass toil at
every step and humans owning every gate. One requirement threads the **whole lifecycle** —
intake → design → build → sit → pt → security-review → deploy → support-handover → done —
sequenced by the reference controller as a saga, correlated end to end into one audit trail.

**Start here:**

```
just demo-sdlc      # the capstone: one requirement through the ENTIRE lifecycle to a shipped,
                    # handed-over change — three human gates, contract locks, one saga timeline
```

The example was built as a program of slices (design build order): slice 2 the reusable
**archetype + skill library**; slice 4 the **one-app (OMS) bounded stage run**; slice 5 the
**controller + stage-graph**; slice 6 the **consolidated design across all three apps** with the
**architect-reviewer harness review**; slice 7 the **harness build + code-review gate**; slice 8
the **cross-app SIT / PT + pre-release security review + defect loop**; and slice 9 (the capstone)
the **deploy package + support handover**, stitching every stage into the full `sdlc-full`
pipeline behind `just demo-sdlc`.

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

First-class `kind: Funnel` artifacts (the gate-funnel composition, design/details/gate-funnel.md),
each referenced by id from a topology node's `funnel:` field. See the
[funnel reference](../../docs/site/reference/funnel.md).

- `consolidated-design-approval` — the design gate: all four layers on the consolidated-design
  artifact — `validate` → `judge` (`artifact-judge`) → `review` (`architect-reviewer`, read-only) →
  multi-party `approve` (oms/web/mobile-lead + infosec-lead).
- `oms-code-review` — the build gate: the `code-review` decision skill → route-back to the harness
  → OMS-lead sign-off.
- `security-review-approval` — the pre-release gate: `validate` → `judge` → `review`
  (`security-consultant` harness) → `infosec-lead` sign-off.
- `deploy-approval` — the **final release sign-off** (slice 9): deterministic `validate` → the
  binding multi-party `approve` by the **eng-manager + cio** (the human-only `release:approve`
  scope, quorum `all`, `min_distinct_approvers: 2`). Deliberately light — the heavyweight harness
  review already ran at the security gate, so this adds the accountable release decision, not
  another investigation.

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

## The harness build (slice 7)

The executor showcase. The `developer` archetype is a **harness executor**
(`executor: { kind: harness, ref: claude-code }`, on the archetype). In `oms-build-harness.yaml`
its node, scoped to the OMS repo, implements the approved design and produces a **candidate diff**
against a demo repo (`fixtures/demo-repo/` — a stub `orders.py` + README). That diff faces the
`oms-code-review` funnel: the `code-review` decision skill judges it, and a below-threshold verdict
routes the critique **back to the harness** for a bounded revision before the OMS lead signs off
(the human approval is the only exit). Swapping the harness — `claude-code` → `opencode` — is a
one-field change on the archetype's `executor.ref`: executors-are-data.

The demo is deterministic (no keys, no network, no real harness). It drives the **real bundled
`claude-code` declarative adapter** — its event-map + interpreter translate a scripted `stream-json`
transcript into normalized `ExecEvent`s (message, tool_call, usage, result-with-diff) — and fakes
only the subprocess launch via `DeclarativeExecutor`'s documented `_open_stream` test seam
(`harness_build.py`). The code-review gate is the slice-6 funnel machinery with a scripted judge +
the real `resolve_multiparty` engine over a file-backed queue. Two scenarios: a clean build that
advances first time, and a finding (unguarded lookup) that routes back to the harness, which revises
and then passes.

```
just demo-harness-build   # harness → candidate diff → code-review gate: clean-advance + finding-route-back
```

## SIT / PT + defect loop (slice 8)

The cross-app test-and-release half, with the controller-driven defect loop as its centerpiece.

**Cross-app SIT + PT against mock rigs.** `sit.yaml` runs the `sit-qa` engineer's end-to-end
business flows frontend→backend **across all three apps** (oms/web/mobile) against a **mock QA rig**;
`pt.yaml` runs the `pt-engineer`'s perf test of the exposed services against a **mock perf rig** and
uses the `pt-analysis` decision skill to judge the samples against the agreed thresholds (pass, or a
regression filed as a defect). The rigs are mocked in `sit_pt.py` — no real systems, no network. Both
topologies read across all three apps (the shared surface) and carry only cross-app read scopes.

**The pre-release security gate.** `security-review.yaml` (a `release-coordinator`) parks on the
`security-review-approval` funnel: deterministic validate → an artifact-judge score → an
investigative **`security-consultant` harness review** (compliance / SAST / DAST — data residency,
authz, injection) whose **HIGH-severity finding routes back** before release (`route_back_at: high`)
→ the `infosec-lead` signs off (the human approval is the only exit). Same four-layer funnel
machinery as slice 6, with the security reviewer as layer 3.

**The controller-driven defect loop.** The `sdlc-sit-pt` stage-graph wires
`build → sit → pt → security-review` with the cross-stage defect cycle (`defect.raised → build`,
`defect.fixed → sit`). The reference controller sequences it: a requirement reaches SIT, SIT raises
`defect.raised` → the controller **re-kicks build**; the fix emits `defect.fixed` → the controller
**re-triggers SIT** → the re-run passes and the saga proceeds through PT and the pre-release security
gate to `done`. SIT/PT completions arrive as enterprise QA/perf events (the mock rigs report results
the same way CI does), so the controller waits on them — which is where the defect is injected. The
demo drives the **reference controller** over a scripted `run_stage` seam (no keys, no network, no
server) and prints the correlated saga timeline showing the loop.

```
just demo-defect-loop   # build → sit → defect.raised → build → sit → defect.fixed → … → done
just demo-sit-pt        # SIT (mock rig) → PT (mock rig, pt-analysis) → security review (harness + infosec sign-off)
```

## The full lifecycle (slice 9) — the capstone

The finale stitches every stage into one pipeline. Two new stages complete the lifecycle:

- `topologies/deploy.yaml` — the `release-coordinator` assembles the **deployment package +
  release notes** from the approved design, the built + code-reviewed diffs, and the SIT / PT
  results, then parks on the `deploy-approval` funnel (the eng-manager + cio final sign-off). It
  assembles and summarises; it does not deploy — the prod deploy authority *is* the human gate.
- `topologies/support-handover.yaml` — the `support-engineer` produces the operator-ready
  **runbook / handover** (what changed, how to operate it, known issues, rollback) and sets up
  prod monitoring. Ungated and terminal: completing it finishes the saga.

`pipelines/sdlc-full.yaml` is the full-lifecycle `kind: StageGraph` — the eight stages in order,
**intake → design → build → sit → pt → security-review → deploy → support-handover → done** —
carrying the two multi-party gates (design, security), the final release gate (deploy), the
integration-contract locks held through design approval, the per-stage compensations, and the
cross-stage defect loop. `roles/sdlc-roles.yaml` gains **eng-manager** (grace) and **cio** (heidi),
conferring the human-only `release:approve` scope.

The capstone demo drives the **reference controller** over `sdlc-full` and takes one requirement
(`OMS-101`) through the ENTIRE lifecycle to a shipped, handed-over change, printing the full
correlated saga timeline (every stage, in order, one `correlation_id`). Deterministic: the three
gated stages (design, security, deploy) park on their gate until a scripted human sign-off; CI +
the mock QA / perf rigs (`build.ready-in-qa` / `sit.passed` / `pt.passed`) arrive externally — no
keys, no network, no server.

```
just demo-sdlc            # the full lifecycle: intake → … → deploy → support-handover → done
just demo-sdlc-stage-run  # the slice-4 one-app bounded stage run (intake → design → judge → approval)
```

## Validate

```
uv run python examples/sdlc-pipeline/validate_library.py
```
