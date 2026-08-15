# Level 16: Sequencing & Contracts

Chain bounded topology runs into long-running delivery work — with human gates, integration locks,
and a run that can wait days for a human without holding anything open. This is a different axis
from Level 12's triggers and canary: those *start* runs, this *sequences* them.

The headline: **SwarmKit does not sequence. Your application does.**

## What you'll learn

- Why the bundled pipeline was removed, and what replaced it
- **Correlating** independent runs — `correlation_id`, `labels`, `parent_job_id`
- **Defer and resume** — a run parks on a human gate and continues later
- Reading a gate with `GET /gates/{gate_id}`, and fetching the artifact under review
- **Contracts** — turning lock ids into a real, checked vocabulary

## The idea

SwarmKit used to ship `kind: StageGraph` plus a durable saga controller, `swarmkit orchestrator`
and `swarmkit pipeline`. All of it was removed in runtime **1.189.0**.

The reason is layering. Sequencing across weeks is *application* logic: what an event means, when to
retry, which business calendar applies, when to give up. Every one of those questions pulled
SwarmKit toward becoming a workflow engine — a crowded field where it had no advantage — and away
from what it is uniquely good at: **one bounded governed run, its gate, and its record**.

So the split is now explicit. You own the sequence. SwarmKit owns the run.

See [Extracting the pipeline](../design-notes/extracting-the-pipeline.md) for the full reasoning and
the migration inventory.

## Build it

Each stage is an ordinary run, started by your application and tagged so the whole flow is one
readable thread:

```bash
swarmkit run . oms-design \
  --correlation-id WMS-35 \
  --label app=oms \
  --input "draft the order API change"
```

Over HTTP, the same thing:

```http
POST /run/oms-design
{"input": "draft the order API change", "correlation_id": "WMS-35", "labels": {"app": "oms"}}
```

- `correlation_id` — **"same ticket."** Groups every run of the flow, including work that is not a
  retry.
- `labels` — opaque `{key: value}` carrying *your* model. SwarmKit never learns what they mean; they
  reach `jobs` **and** `audit_events`.
- `parent_job_id` (`--supersedes` on the CLI) — **"this replaces that attempt."** A redo after a
  rejected artifact is a *new* job, so the chain is what makes "what did this artifact really cost"
  answerable across retries.

## Gate it, and walk away

Put a [Funnel](../reference/funnel.md) on the agent that produces the artifact. When the run reaches
the `approve` layer it **defers**: the graph checkpoints, the job goes `deferred`, and nothing stays
resident while a human decides.

```json
{"job_id": "a46614b1", "status": "deferred", "error": "awaiting review: gate 'a46614b1:designer'"}
```

Your application polls the gate — with the approval policy **already applied**, so quorum,
distinct-approver floors and author exclusion are the runtime's own decision, not a fold your client
invented:

```http
GET /gates/a46614b1:designer
```

```json
{"gate_id": "a46614b1:designer", "status": "approved", "quorum_evaluated": true,
 "artifact_ref": "WMS-35/a46614b1/output"}
```

A gate id is `<run_id>:<agent_id>` — the run id is the job id. Split on the **last** colon.

Fetch what is being approved with `GET /artifacts/WMS-35/a46614b1/output`; an approver deciding
without the artifact is deciding on a title.

Then continue the run:

```http
POST /jobs/a46614b1/resume
```

or locally, `swarmkit run . oms-design --resume a46614b1`. A resumed run can park again, and does so
identically.

## Lock the interfaces

A lock **is** an integration [Contract](../reference/contract.md) (`kind: Contract`) naming the apps
it binds — so two pieces of work that both touch the OMS↔Web order API don't design concurrently.
SwarmKit makes the vocabulary real (the resolver rejects a lock naming no contract, so a typo can't
silently become a *different* lock); your sequencer is the lock manager.

## Run it

```bash
just demo-sdlc-stage-run       # one gated stage, end to end
just demo-consolidated-design  # three architects → a synthesizer → a four-layer funnel
```

And the reference application that sequences runs itself:

```bash
python examples/pipeline-orchestrator/run_pipeline.py
```

Read [`examples/pipeline-orchestrator/`](https://github.com/delivstat/swarmkit/tree/main/examples/pipeline-orchestrator) —
it drives a multi-stage flow with **no `swarmkit_runtime` import anywhere in it**. That is the proof
the seam is honest: if the reference orchestrator needed a private import, the boundary would be a
claim rather than a fact.

## What happened

Your code decided what runs next. SwarmKit ran each bounded stage under governance, parked the ones
with a human gate without holding a process open, recorded every run against one correlation id, and
kept the artifacts and the audit trail that let you reconstruct the whole flow afterwards.

## Learn more

- [Driving SwarmKit from your application](../reference/orchestrator-integration.md) — the whole HTTP contract in one page
- [Reading a gate, and approving without a saga](../design-notes/gate-state-and-deferring-approval.md)
- [Contract artifact reference](../reference/contract.md) · [Funnel artifact reference](../reference/funnel.md)
- [SDLC walkthrough](../sdlc-example/) — the workspace on video (recorded before the extraction; the artifact tour is current, the stage-graph sections are historical)
