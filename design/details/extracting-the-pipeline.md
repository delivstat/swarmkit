# Taking the pipeline out of the runtime

**Status:** proposed — design only. The *why* is settled in
[`finishing-the-orchestration-seam.md`](finishing-the-orchestration-seam.md); this note is the
*how*: what actually leaves, what cannot leave yet, and in what order.

## What this is

Removing stage sequencing from `swarmkit-runtime` and `swarmkit serve`. Topologies, archetypes,
skills and funnels stay. An application sequences runs itself, joining them with a correlation id.

It is **not** a rewrite. Most of the surface is additive and lifts out; the interesting part is the
three pieces that do not, and the order they force.

## Inventory

| component | LOC | verdict |
| --- | --- | --- |
| `orchestration/` (saga state, store, reference controller) | 1169 | leaves |
| `server/_routes_pipelines.py` | 407 | leaves |
| `server/_routes_sagas.py` | 89 | leaves |
| `server/_pipeline_stage.py` | 439 | **blocked** — holds the only `open_gate` call |
| `cli/_cmd_pipeline.py`, `cli/_cmd_orchestrator.py` | — | leaves |
| `triggers/_pipeline_ingress.py` | 77 | **stays**, reframed |
| `gate_coverage.py` + `cli/_cmd_gates.py` + `GET /pipelines/{id}/gate-coverage` | — | **splits** |
| UI `/runs`, `/pipelines`, `lib/gate-coverage.ts` | — | leaves |
| `persistence`: `StoreKind.SAGA`, `saga_store()` | — | leaves |
| `schemas/stage-graph.schema.json` | — | leaves, last |
| `deploy/pipeline` | — | leaves |

Roughly 2,100 lines of runtime plus two UI pages. The reference app that replaces it should be a
fraction of that, because most of what leaves is durability the application no longer owns.

## The four categories

### 1. Leaves cleanly

Routes, CLI commands, UI pages, the controller and the saga store are additive: nothing outside the
pipeline reads them. `ReferenceController` imports only `RunStage`, `StageOutcome`, `SagaState` and
`SagaStore` — all from its own package.

### 2. Stays, and gets a better name

`PipelineSignal` is not an engine, it is a **type alias** — `Callable[[str, str], Awaitable[None]]`.
`_routes_jobs.py` and `server/_mcp.py` depend on the *shape*, so a webhook or an MCP tool can emit an
event without knowing what consumes it. Together with `triggers/_pipeline_ingress.py` (77 lines,
correlation extraction and signature checking) that is the **inbound integration seam**, and it is
exactly what an application-owned orchestrator needs to be driven by.

It should survive under a name that does not say "pipeline" — the thing it does is *deliver a
correlated external event to whatever is listening*, which is useful whether or not SwarmKit
sequences anything.

### 3. Cannot leave yet

`server/_pipeline_stage.py` is not routing. It threads upstream artifacts into a stage's input, and
it **opens the funnel gate** — the only `open_gate` call in a run path. Delete it today and gates
stop existing.

Its replacement is Part 2 of
[`gate-state-and-deferring-approval.md`](gate-state-and-deferring-approval.md): once the funnel's
approve layer opens its own gate and defers, `_pipeline_stage` holds nothing that is not either
sequencing (leaves) or artifact threading (which the application does with
`--correlation-id` and `GET /artifacts/{ref}`).

**This is the whole dependency.** Extraction is blocked on the gate work, and on nothing else.

### 4. Splits in two

`gate_coverage` computes "the narrowest verified edge" over a StageGraph. With no stage graph there
are no edges — so the pipeline half goes, and `swarmkit gates --require` goes with it, which is a
real loss for anyone gating CI on it.

But the analysis has a second half that is not pipeline-shaped at all: **which agents carry a funnel,
and how strong is it** (`validate` / `judge` / `review` present, `approve` policy). That question is
about topologies and funnels, both of which stay, and it is the natural sibling of the reachability
report — *"this topology's output is verified by nothing"* is the same class of finding as *"this
binding is reached by nothing"*.

Recommendation: keep the per-agent funnel-strength analysis, retire the per-stage edge analysis with
the stage graph, and fold the survivor into `swarmkit validate` beside reachability rather than
leaving a `gates` command whose subject has left.

## The order, which is not negotiable

1. **Gate work** — `GET /gates/{gate_id}`, approve-defers, gate-id unification, the UI link
   (all of `gate-state-and-deferring-approval.md`). Approval stops depending on a saga.
2. **HTTP parity** — `correlation_id`/`labels` on `RunRequest`, `GET /artifacts/{ref}`,
   `POST /jobs/{job_id}/resume`. An application can now drive a sequence over the API.
3. **`_pipeline_stage.py` becomes removable** — it holds nothing unique once 1 and 2 land.
4. **The reference app** proves the boundary by running the `sdlc-pipeline` example through the
   public API only.
5. **Deprecate, then delete.**

Steps 1 and 2 are worth doing whether or not the extraction ever happens, which is the argument for
starting there: nothing is wasted if the decision reverses.

## Where it goes

`examples/pipeline-orchestrator/` — a reference application, not a distributed package.

A package (`swarmkit-orchestrator`) is the more complete-sounding answer and probably the wrong one:
it is a maintenance commitment to an engine this note argues SwarmKit should not be in the business
of, and the people most likely to adopt this — including the WMS application — are writing their own
driver against their own tracker, not installing ours.

`examples/sdlc-pipeline/orchestrator/temporal/` already exists and shows the Temporal shape of the
same loop; what it lacks is the HTTP form, because step 2's calls do not exist yet. Converting it to
use only the public API is the acceptance test for the whole extraction.

## What existing deployments do

Nothing, for at least one release. The bundled controller keeps working, keeps getting bug fixes, and
is documented as what it already calls itself: a *reference* sequencer for simple linear pipelines.

Capability freezes immediately — no event routing, no fan-out, no cycles. A workspace that needs those
is a workspace that should own its sequencing, and that signal is more useful than the feature.

## Data

- **`pipeline_saga` leaves** with the controller.
- **`pipeline_artifacts` stays.** Since 1.179.0 a one-shot run writes to it, so it is the general
  artifact store with a misleading name. Renaming a table is a migration for a cosmetic gain; the
  honest minimum is documenting that the name is historical.
- Job rows, audit events and artifacts written by pipeline runs stay readable. Extraction must not
  orphan history: a `<correlation>:<stage>` job id keeps resolving after the sequencer is gone.

## Non-goals

- Not removing funnels, gates, or the review queue — the opposite; Part 2 makes them independent.
- Not prescribing an engine.
- Not deleting `stage-graph.schema.json` before the bundled controller goes.
- Not changing how a run executes.

## Test plan

- Every pipeline surface removed has a test asserting the *runtime* still works without it: a
  funnel-gated one-shot run approves and resumes with no saga store present at all.
- **A workspace with no orchestrator configured can still gate** — the assertion that step 1 landed.
- History survives: a job row, audit events and artifacts written under `<correlation>:<stage>` are
  still fetchable after the sequencer is removed.
- The funnel-strength half of `gate_coverage` still reports; the stage-edge half is gone and
  `swarmkit gates` says where it went rather than 404-ing on a missing pipeline.
- The reference app drives the `sdlc-pipeline` example end to end, with one human approval, importing
  no `swarmkit_runtime` module.
- The inbound signal seam still delivers a correlated webhook event after the pipeline routes leave.

## Demo plan

The `sdlc-pipeline` example run twice — once on the bundled controller, once on the reference app
over HTTP — producing the same artifacts, the same approval, and one correlated trail in
`swarmkit logs`. Then the same run with `orchestration/` uninstalled, showing gates unaffected.

## Open questions for review

1. **Reference app or distributed package?** The note argues for `examples/`. A package makes the
   migration one install line for anyone on the bundled controller today.
2. **Does `stage-graph.schema.json` stay in `packages/schema`?** If sequencing is the application's,
   the format arguably is too — but a shared format is how the reference app and the bundled
   controller stay compatible during the transition.
3. **What replaces `swarmkit gates --require` for CI?** The funnel-strength check can gate on "every
   agent producing an artifact carries a funnel", which is close but not the same guarantee.
4. **Is one release enough deprecation** for a subsystem someone is running in production?
