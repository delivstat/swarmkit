# Level 16: Sequencing & Contracts

Chain bounded runs into work that spans days — with a human gate in the middle, a run that waits
for that human without holding anything open, and a record that ties every run to one ticket.
This is a different axis from Level 12's triggers and canary: those *start* runs; this
*sequences* them.

The headline: **SwarmKit does not sequence. Your application does.**

## What you'll learn

- Why the bundled pipeline was removed, and what replaced it
- **Correlating** independent runs — `correlation_id`, `labels`, `parent_job_id`
- **Defer and resume** — a run parks on a human gate and continues when it resolves
- Reading a gate with `GET /gates/{gate_id}`, fetching the artifact under review, resolving as
  a named person over HTTP
- **Contracts** — turning lock ids into a real, checked vocabulary
- The reference orchestrator: a sequencer with no runtime import

The finished workspace is `examples/tutorials/16-pipelines/` — Level 15 plus a `contracts/`
directory and two more API keys. Everything below ran against a real serve.

## The idea

SwarmKit used to ship `kind: StageGraph`, a durable saga controller, `swarmkit orchestrator` and
`swarmkit pipeline`. All of it was removed in runtime **1.189.0**.

The reason is layering. Sequencing across weeks is *application* logic: what an event means, when
to retry, which business calendar applies, when to give up. Every one of those questions pulled
SwarmKit toward becoming a workflow engine — a crowded field where it had no advantage — and away
from what it is uniquely good at: **one bounded governed run, its gate, and its record**.

So the split is explicit. You own the sequence. SwarmKit owns the run. See
[Extracting the pipeline](../design-notes/extracting-the-pipeline.md) for the full reasoning.

## Build it

### 1. A correlated run

Each stage is an ordinary run, started by your application and tagged so the whole flow is one
readable thread. Level 7's `analysis` topology — one analyst, a structured verdict, the
`design-gate` funnel that needs two leads — is a fine stage:

```bash
curl -s -X POST -H "Authorization: Bearer $APP_TOKEN" -H 'content-type: application/json' \
  localhost:8000/run/analysis -d '{
    "input": "Should we raise the travel meal allowance from 2,500 to 3,500 rupees per day? Consider hotel prices in Mumbai and Bengaluru and the finance portal impact.",
    "correlation_id": "HB-88",
    "labels": {"app": "handbook", "ticket": "HB-88"}
  }'
```

```json
{"job_id": "ab633a5ba635", "status": "pending", "topology": "analysis", "correlation_id": "HB-88", …}
```

- `correlation_id` — **"same ticket."** Groups every run of the flow, including work that is not a
  retry. `GET /jobs/history` and `GET /artifacts?correlation_id=HB-88` read it back.
- `labels` — opaque `{key: value}` carrying *your* model. SwarmKit never learns what they mean;
  they reach `jobs` **and** `audit_events`.
- `parent_job_id` (`--supersedes` on the CLI) — **"this replaces that attempt."** A redo after a
  rejected artifact is a *new* job, so the chain is what makes "what did this artifact really
  cost" answerable across retries.

On the CLI: `swarmkit run . analysis --correlation-id HB-88 --label app=handbook --input …`.

### 2. It parks on the gate

The analyst answered, the judge scored it, and the funnel's `approve` layer opened the gate. The
run **deferred**: the graph checkpointed, the job closed, nothing stays resident while people
decide.

```json
{"job_id": "ab633a5ba635", "status": "deferred",
 "error": "awaiting review: gate 'ab633a5ba635:analyst' awaits approval — role-tasks opened on the review queue"}
```

### 3. Read the gate

A gate id is `<run_id>:<agent_id>`; the run id is the job id. Your application polls it — with the
approval policy **already applied**, so quorum, distinct-approver floors and author exclusion are
the runtime's decision, not a fold your client invented:

```bash
curl -s -H "Authorization: Bearer $APP_TOKEN" localhost:8000/gates/ab633a5ba635:analyst
```

```json
{
  "gate_id": "ab633a5ba635:analyst",
  "status": "pending",
  "resolved": false,
  "funnel_id": "design-gate",
  "run_id": "ab633a5ba635",
  "artifact_ref": "ab633a5ba635:analyst#50798749ac4e",
  "outstanding": ["engineering-lead (design:approve)", "product-lead (design:approve)"],
  "distinct_approvers": [],
  "resolutions": [
    {"item_id": "mpa-ab633a5ba635:analyst-0-engineering-lead", "role": "engineering-lead", "status": "pending"},
    {"item_id": "mpa-ab633a5ba635:analyst-0-product-lead",     "role": "product-lead",     "status": "pending"}
  ]
}
```

`status` is the one field a driver must understand. `GET /review` lists the same role-tasks, each
carrying `run_id` — which is how an application finds the gate for the job it started, without
guessing at id shapes.

### 4. Fetch what is being approved

An approver deciding without the artifact is deciding on a title. The gate's `artifact_ref` is
fetchable (URL-encode it — it contains `:` and `#`):

```bash
curl -s -H "Authorization: Bearer $APP_TOKEN" "localhost:8000/artifacts/ab633a5ba635%3Aanalyst%2350798749ac4e"
```

```json
{"ref": "ab633a5ba635:analyst#50798749ac4e",
 "content": "{\n  \"confidence\": 0.8,\n  \"findings\": [\n    \"Mumbai average meal costs rose 25% in 2023, now averaging 3,200 rupees/day in business districts.\", …],\n  \"recommendation\": \"approve\"\n}",
 "length": 612}
```

### 5. Resolve — as a person, over HTTP

A resolution's identity is the **authenticated caller**, never a name in the request body: one
operator cannot satisfy an N-of-N policy by resolving each role-task under a different name. So
each approver has their own key, and `client_id` must match their entry in the role registry:

```yaml
# workspace.yaml — server.auth.config.keys, two more entries
        - key_ref: env:ALICE_TOKEN
          client_id: alice                    # a member of engineering-lead in roles/leads.yaml
          client_name: Alice (engineering lead)
          tier: run
        - key_ref: env:BOB_TOKEN
          client_id: bob                      # a member of product-lead
          client_name: Bob (product lead)
          tier: run
```

```bash
curl -s -X POST -H "Authorization: Bearer $ALICE_TOKEN" -H 'content-type: application/json' \
  localhost:8000/review/mpa-ab633a5ba635:analyst-0-engineering-lead/resolve \
  -d '{"outcome": "approve", "comment": "numbers check out against the finance portal"}'
curl -s -X POST -H "Authorization: Bearer $BOB_TOKEN" -H 'content-type: application/json' \
  localhost:8000/review/mpa-ab633a5ba635:analyst-0-product-lead/resolve \
  -d '{"outcome": "approve", "comment": "numbers check out against the finance portal"}'
```

```json
{"gate_id": "ab633a5ba635:analyst", "status": "approved", "resolved": true,
 "distinct_approvers": ["alice", "bob"], "outstanding": [], …}
```

`outcome` is `approve`, `changes-requested` or `reject`; `comment` is relayed to the agent and
recorded on the audit. (`swarmkit review resolve <id> --as alice --approve` is the CLI form,
Level 7.)

### 6. It continues by itself

The moment the second approval landed, the run resumed and finished — `gates.auto_resume` is on by
default:

```json
{"job_id": "ab633a5ba635", "status": "completed",
 "output": "{\n  \"confidence\": 0.8,\n  \"findings\": [ … ],\n  \"recommendation\": \"approve\"\n}"}
```

The gated node returns the text the approvers saw; it does not draft again. `POST /jobs/{id}/resume`
is for an application that wants to hold the continuation itself
(`gates: {auto_resume: false}` in `workspace.yaml`), or for a run stopped by hand; calling it on a
run that is already moving answers 409, which a sequencer treats as "fine, keep polling". A
resumed run can park again, identically.

```bash
curl -s -H "Authorization: Bearer $APP_TOKEN" localhost:8000/jobs/history | jq '.[] | select(.correlation_id=="HB-88")'
```

```
ab633a5ba635  analysis  HB-88  completed
```

## Lock the interfaces

A lock **is** an integration [Contract](../reference/contract.md) (`kind: Contract`) naming the
apps it binds — so two pieces of work that both touch the same interface don't proceed
concurrently. SwarmKit makes the vocabulary real: the resolver rejects a lock naming no contract,
so a typo cannot silently become a *different* lock. Your sequencer is the lock manager.

```yaml
# contracts/handbook-finance.yaml
apiVersion: swarmkit/v1
kind: Contract
metadata:
  id: handbook-finance
  name: Handbook ↔ Finance portal
  description: >
    The expense rules the finance portal enforces (limits, receipt thresholds, payroll timing)
    and the handbook pages that state them. A change to either side is a change to this contract.
parties: [handbook, finance-portal]
provenance:
  authored_by: human
  version: 1.0.0
```

```bash
curl -s -H "Authorization: Bearer $APP_TOKEN" localhost:8000/contracts
```

```json
["handbook-finance", "handbook-oncall"]
```

A contract is never executed. Its `parties` let your lock manager group work by the app-pair it
binds; the portal's **Contracts** page lists them and their YAML is editable there.

## The reference orchestrator

`examples/pipeline-orchestrator/` is a sequencer over the five endpoints above — start, poll,
find the gate, poll the gate, continue — with **no `swarmkit_runtime` import anywhere in it** (a
test enforces that). Pointed at this workspace with two stages, research feeding a gated decision:

```python
PIPELINE = (
    Stage(id="research", topology="librarian"),
    Stage(id="decision", topology="analysis", after=("research",)),
)

def build_input(run, stage):
    if stage.id == "research":
        return "What does the handbook say about meal allowances and alcohol while travelling? Cite the file."
    return ("Should we raise the travel meal allowance from 2,500 to 3,500 rupees per day? "
            "Here is what the handbook says today:\n" + run.artifacts["research"])

run = run_pipeline(client, "HB-89", PIPELINE, build_input=build_input)
```

```
--- research (8c70e8803b64) ---
The handbook's expense policy says … up to **₹2,500 per day** … Alcohol is not reimbursable.
*Source: knowledge/docs/expense-policy.md*
--- decision (d3c8965b1645) ---
{ "confidence": 0.8, "findings": [ … ], "recommendation": "approve" }
```

Threading the research into the decision is the application's line of code, not a SwarmKit
feature — which is the point. Both runs sit under `HB-89` in the history, the second one parked
on its gate until the leads resolved it.

## What happened

Your code decided what runs next. SwarmKit ran each bounded stage under governance, parked the one
with a human gate without holding a process open, resumed it when the policy was satisfied,
recorded every run against one correlation id, and kept the artifact and the audit trail that let
you reconstruct the flow afterwards.

## Learn more

- [Driving SwarmKit from your application](../reference/orchestrator-integration.md) — the whole HTTP contract in one page
- [Reading a gate, and approving without a saga](../design-notes/gate-state-and-deferring-approval.md)
- [Contract artifact reference](../reference/contract.md) · [Funnel artifact reference](../reference/funnel.md)
- [SDLC walkthrough](../sdlc-example/) — the workspace on video (recorded before the extraction; the artifact tour is current, the stage-graph sections are historical)

## Next

[Level 17: Harness executors](17-harness-executors.md) — Claude Code or opencode as a node in a topology.
