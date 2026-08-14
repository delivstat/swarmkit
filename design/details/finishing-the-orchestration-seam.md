# Finishing the orchestration seam

**Status:** proposed — design only. Extends
[`orchestration-provider-seam.md`](orchestration-provider-seam.md) (status: partially-implemented);
does not re-argue it. Supersedes `event-routed-stage-graphs.md`, which should not be implemented.

## What is already decided, and already built

The boundary decision exists. The ADR made the call — don't rebuild Temporal inside SwarmKit;
delegate durable sequencing behind an `OrchestrationProvider` seam — selected Temporal, and kept the
reference controller as the zero-infra implementation.

A reference external orchestrator exists too: `examples/sdlc-pipeline/orchestrator/temporal/`, with a
workflow and an adapter, and both it and the bundled controller read `compensation`.

So "can something else sequence SwarmKit runs?" is answered. Two things are unfinished, and together
they are why the answer does not yet feel true.

## 1. The seam is in-process; the boundary is not

`temporal/_adapter.py` imports `RunStage` and `SagaView` directly and takes a callable. It is a
pluggable **sequencer inside a Python process that imports the runtime** — which proves the seam,
not the boundary.

An orchestrator that is genuinely a separate application — another language, another process,
calling over HTTP — cannot do today what the CLI can:

- **`RunRequest` carries only `input` and `max_steps`.** Serve's `create_job` passes `None` for
  correlation, so a run started over HTTP cannot be correlated or labelled. That is bug #1 again, on
  the surface an external application actually uses.
- **No artifact HTTP routes.** `swarmkit artifacts list|get` landed in 1.179.0; there is no
  `GET /artifacts/{ref}`, so stage-to-stage threading is CLI-only.
- **No resume endpoint.** `swarmkit run --resume` exists; an application that started a run over
  HTTP and saw it defer cannot continue it over HTTP.

The review surface is *almost* complete (`GET /review`, `POST /review/{id}/approve|reject`) — the
missing piece is a gate-level read, see the walkthrough below. These are all small, and together
they are the difference between "pluggable sequencer" and "the application owns sequencing".

## 2. The spec is written in the reference controller's vocabulary

The ADR says the engine *interprets* the `StageGraph`. But the stage schema carries `locks`,
`release_locks_on` and `compensation` — saga-engine internals, not neutral sequencing concepts.
Temporal has no lock primitive; you build a mutex workflow. Compensation is a hand-written pattern.
Another engine models both differently again.

So an adapter must reimplement each feature or quietly ignore it, and **nothing declares which, and
nothing checks.** The ADR's conformance suite tests that adapters implement the seam; it does not
test that *this graph's* declared features are honoured by *the engine actually running it*.

That is the defect shape this codebase has hit five times — declared, validated, displayed, executed
by nothing — one layer up, and with a worse failure. A `validate:` block nothing wires produces an
unchecked artifact. A `locks:` block an engine ignores produces **two stages running concurrently
against a contract that said they must not**, and nothing anywhere says so.

### The proposal: a declared capability set, checked against the graph

`OrchestrationProvider.capabilities()` returns what it honours — `locks`, `compensation`, `timers`,
`signals`, `gates`. At graph load, every stage feature the configured provider does not declare is
reported; under `--require`, refused.

This is deliberately the *same* machinery as the reachability report: same shape, same surfaces
(`swarmkit validate`, serve startup, an HTTP route), same `--require` semantics. A stage feature no
engine will execute is a declared-but-unreachable finding, and the report already exists.

It also makes the spec honest in the other direction. If no engine can honour `locks`, that is the
evidence for moving it out of the schema rather than a reason to keep validating it.

## 3. Approval must not depend on there being a saga

The funnel's `approve` layer is currently advisory — `build_advisory_approver` records and passes —
justified in 1.172.0 as:

> Human approval on the pipeline path is the stage-level `gate:`, which parks the saga durably.

Under the seam that reasoning fails. The whole point is that the application may own sequencing, in
which case there may be no saga and no stage gate — so approval would be unavailable on exactly the
path the ADR steers people toward.

It was already the weaker branch. The choice was framed as *block the coroutine for seven days*
(`resolve_multiparty` polling in-node) or *pass advisorily*; rejecting the block was right, but
**defer-and-resume already existed and beats both.** `HITLDeferredError` checkpoints the graph, closes
the job as `deferred`, exits cleanly, and `swarmkit run --resume` continues it after
`swarmkit review approve` — per run, durable, no orchestrator involved.

**The funnel's approve layer should open the gate, checkpoint, and defer.** On resume: proceed if the
gate resolved, defer again if it did not.

Then approval works identically under the bundled controller, under Temporal, and under a shell
script — which is the property the seam needs and does not have. Approval moves onto the
**artifact**, where a funnel already judges it, instead of the **edge**, where a stage gate approves
a transition without knowing what it contains.

The 1.172.0 guard removal stays correct: `validate` and `judge` never should have depended on a
review queue. Only the approver changes.

## How an external orchestrator actually implements a pipeline

The application owns a loop over stages. Per stage it runs this state machine:

```
 START ──► POST /run/{topology}          ──► job_id
           │
           └─► poll GET /jobs/{job_id}
                 running    ─► keep polling
                 completed  ─► take the artifact ─► next stage
                 deferred   ─► an approval is pending ─► AWAITING
                 failed     ─► the application's retry policy

 AWAITING ─► poll GET /gates/{gate_id}
                 pending    ─► keep polling (days is normal)
                 approved   ─► POST /jobs/{job_id}/resume ─► back to polling
                 rejected   ─► the stage fails
```

Nothing about that is SwarmKit-specific: it is start, poll, wait-for-human, resume. A Temporal
workflow expresses it with activities and a signal; a shell script expresses it with `curl` and
`sleep`. That is the point — the sequencing is ordinary, and only the run and the approval are not.

### The calls

| step | call | notes |
| --- | --- | --- |
| start a stage | `POST /run/{topology}` → `{job_id, status}` | returns immediately; the run is async |
| watch it | `GET /jobs/{job_id}` | `running` / `completed` / `failed` / `deferred` |
| stream it | `GET /jobs/{job_id}/stream` | optional, for progress rather than control flow |
| find the approval | `GET /review?gate_id=<gate>` | the funnel's role-tasks for that gate |
| approve / reject | `POST /review/{item_id}/approve` (or `/reject`), body `{comment}` | a human, or the application's own UI |
| continue | `POST /jobs/{job_id}/resume` | after the gate resolves |
| thread the output | `GET /artifacts/{ref}` | the next stage's input |

The gate id is derivable rather than discovered: the funnel gate is `"{topology_id}:{agent_id}"`, so
an orchestrator that knows which topology it started knows which gate to watch.

### How approval works, end to end

1. The stage's agent produces an artifact. The funnel validates and judges it, retrying the drafter
   on failure.
2. At the `approve` layer the funnel fans its policy into **role-tasks on the review queue** and the
   run **checkpoints and defers**: the job closes as `deferred`, no process is held open, and the
   state survives a restart. *(This is the change proposed above; today the layer passes advisorily.)*
3. The orchestrator sees `deferred`, and polls the gate.
4. A human resolves each role-task. Quorum, distinct-approver counts and exclude-author are applied
   **server-side** — see the gap below.
5. On resolution the orchestrator resumes the job. The run continues from its checkpoint, inside the
   same agent node, with the approved artifact.

The application never learns what a funnel is, what a role is, or how quorum works. It learns one
thing: *this job is waiting on gate G, and G is not resolved yet.*

### The gaps this exposes

Four are the HTTP additions listed above. Answering "how would an orchestrator actually do this?"
surfaces a fifth, and it is the most important:

- **`GET /gates/{gate_id}` — the gate's resolved state, with the policy applied.** Today an
  orchestrator polling `GET /review?gate_id=…` sees *individual role-tasks*. Deciding whether the
  gate is resolved means re-implementing quorum, distinct-approver counting and exclude-author —
  logic that lives in `resolve_multiparty` and is exactly the SwarmKit-shaped part an application
  must not rebuild. Without this endpoint, every external orchestrator either reimplements the
  approval policy or approximates it, and an approximation of an approval policy is a governance
  failure with a friendly name.

So the full checklist for an out-of-process orchestrator:

1. `correlation_id` + `labels` on `RunRequest`;
2. `GET /artifacts/{ref}`;
3. `POST /jobs/{job_id}/resume`;
4. `GET /gates/{gate_id}` returning `pending | approved | rejected` with the policy applied;
5. the funnel approve layer deferring rather than passing.

Items 1–4 are small. Item 5 is the one that makes approval work at all off the saga path.

### A reference implementation, in full

```python
for stage in pipeline:                       # the application's own list
    body = {"input": build_input(stage, artifacts), "correlation_id": ticket}
    job = post(f"/run/{stage.topology}", body)["job_id"]

    while (status := get(f"/jobs/{job}")["status"]) in {"running", "deferred"}:
        if status == "deferred":
            gate = f"{stage.topology}:{stage.agent}"
            if (state := get(f"/gates/{gate}")["status"]) == "approved":
                post(f"/jobs/{job}/resume")
            elif state == "rejected":
                raise StageRejected(stage)
        sleep(POLL)

    if status != "completed":
        raise StageFailed(stage)
    artifacts[stage.id] = get(f"/artifacts/{ticket}/{job}/output")
```

That is the whole integration. If a reference application needs materially more than this, the
boundary is in the wrong place and this note is wrong — which makes the reference app a test of the
design rather than a decoration on it.

`examples/sdlc-pipeline/orchestrator/temporal/` already has the Temporal shape of the same loop; what
it does not have is the HTTP form, because the calls above do not all exist yet.

## Consequences

- `gate:` on a stage becomes redundant. Keep it — removing it breaks every existing graph — document
  it as redundant, do not extend it.
- No event routing, no fan-out, no cycles in the bundled controller. A workspace needing those is a
  workspace that should be on a real engine, and that signal is more useful than the feature.
- The bundled controller stays as the zero-infra option and keeps getting bug fixes. Freezing
  capability is not deprecation.

## Non-goals

- Not re-deciding the seam, the engine, or whether the reference controller lives.
- Not deleting the saga store or the stage-graph schema.
- Not changing how a run executes or what a funnel validates.

## Test plan

- **Approval:** a run whose funnel gate is unresolved closes as `deferred`, writes a review item, and
  holds no process; resuming after approval completes it; resuming while still unapproved defers
  again rather than proceeding; a rejection surfaces as a gate rejection.
- **Parity:** the same funnel-gated topology approves identically when driven by the bundled
  controller, by the Temporal example, and by a bare `swarmkit run` loop.
- **Capabilities:** a graph declaring `locks` against a provider that does not declare `locks` is
  reported by `validate` and refused under `--require`; a graph within capability is silent.
- **HTTP parity:** correlation and labels set on `POST /run/{topology}` reach `jobs` and
  `audit_events`; an artifact written by one run is fetchable over HTTP by the next; a deferred run
  resumes over HTTP.
- An out-of-process orchestrator drives a two-stage pipeline with one approval, with no
  `swarmkit_runtime` import anywhere in it.

## Demo plan

The `sdlc-pipeline` example run three ways — bundled controller, Temporal, and a ~100-line HTTP
script — producing the same artifacts and the same approval, correlated under one id in
`swarmkit logs`. Plus `swarmkit validate` refusing a graph whose `locks` the configured engine does
not honour.

## Open questions for review

1. **Do `locks` / `compensation` / `release_locks_on` belong in the schema at all?** The capability
   check makes their status visible; it does not decide it. If Temporal is the recommended engine and
   it models both differently, the honest move may be to drop them from the spec and let the engine's
   own configuration express them.
2. **Where do capabilities live** — code on the provider, or a declared manifest beside the adapter?
   Adapters are already declarative for harnesses (`adapter.yaml`), and the same argument applies.
3. **Should the bundled controller get a deprecation date?** Zero-config pipelines were a deliberate
   product decision and that value does not disappear because the layering is wrong.
4. **Does `deferred` need a webhook** rather than polling? An external orchestrator polling
   `GET /review` works, and the ADR already lists callback gate notification as pending.
