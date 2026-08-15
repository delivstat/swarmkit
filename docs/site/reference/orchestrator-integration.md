# Driving SwarmKit from your application

The contract between `swarmkit serve` and whatever drives it — your application, a Temporal worker,
an Airflow DAG, a shell script. Everything here is HTTP; the runtime holds **no sequencing state of
its own**.

SwarmKit used to ship a pipeline sequencer (`kind: StageGraph`, a saga controller, `swarmkit
orchestrator`, `swarmkit pipeline`, `POST /pipelines/*`). It was removed in runtime 1.189.0 — see
[Extracting the pipeline](../design-notes/extracting-the-pipeline.md) for why, and
[`examples/pipeline-orchestrator/`](https://github.com/delivstat/swarmkit/tree/main/examples/pipeline-orchestrator)
for a reference application that sequences runs with **no `swarmkit_runtime` import anywhere in
it**. What SwarmKit keeps is the part it is good at: one bounded governed run, its gates, and its
record.

If you only read one section, read [Approval gates](#approval-gates) — a client that assumes a gate
is a boolean will misreport its state.

## The surface

| Endpoint | Purpose |
| --- | --- |
| `POST /run/{topology}` | Start one bounded run; returns a job id |
| `GET /jobs/{job_id}` | Status, output, usage, diff length, correlation, labels, parent |
| `GET /jobs/{job_id}/stream` | SSE progress for a running job |
| `GET /jobs/{job_id}/diff` | The unified diff a harness run produced |
| `POST /jobs/{job_id}/resume` | Continue a run parked on a human gate, or stopped by a human |
| `POST /jobs/{job_id}/stop` | Ask a running job to stop at its next agent boundary |
| `GET /gates/{gate_id}` | Is this gate resolved, **with the approval policy applied** |
| `GET /review` | What is waiting for a human |
| `POST /review/{item_id}/resolve` | Cast one multi-party role-task |
| `GET /artifacts/{ref}` | Fetch the artifact a gate is about |
| `POST /events/signal` | Deliver an inbound event (webhook ingress) to your own listener |

### Correlating a chain of runs

Runs are **independent, connected by a correlation id** — not stages of a pipeline the runtime
knows about. Your application owns the sequence; SwarmKit records the thread.

```http
POST /run/wms-design
{"input": "draft the API", "correlation_id": "WMS-35", "labels": {"map": "wayfinder-7"}}
```

- `correlation_id` — "same ticket". Different units of work *and* retries share it.
- `labels` — opaque `{key: value}` your application groups by. SwarmKit never learns what they mean;
  they reach `jobs` **and** `audit_events`.
- `parent_job_id` — "this replaces that". A re-run is a **new job**, so the chain is what makes
  "what did this artifact really cost" answerable across attempts.

`GET /jobs?correlation_id=WMS-35` lists the thread. Walk `parent_job_id` to see the attempts.

Do not reuse a job id: it keys the LangGraph checkpoint thread, so a reused id inherits the previous
run's state.

## Parking on a human, and resuming

A funnel's `approve` layer **defers the run** rather than holding a process open: the run
checkpoints, the job goes `deferred`, and its `error` names the gate.

```json
{"job_id": "a46614b1", "status": "deferred", "error": "awaiting review: gate 'a46614b1:designer'"}
```

Nothing has to stay resident. When the gate resolves, continue it:

```http
POST /jobs/a46614b1/resume
```

A `deferred` or `stopped` job resumes — both are parked mid-flight with their state on the
checkpoint, and only the reason differs. A completed run has nothing to continue, and starting a
second execution against one checkpoint would interleave two runs on it (409 otherwise). A resumed
run can park **again**, and does so identically.

### Stopping one

```http
POST /jobs/a46614b1/stop
```

Writes the same durable flag `swarmkit stop` writes — one mechanism, two front doors. **Cooperative,
not a kill**: the run stops between agents, so a harness session or a slow tool call in flight
finishes first, and everything already done stays on the checkpoint. The job goes `stopped` (not
`failed` — nothing went wrong; not `deferred` — it waits on nothing) and resumes like any parked run.
Asking twice is not an error, and a resume clears the request so the run does not immediately
re-stop.

Locally the same thing is `swarmkit run <topology> --resume <job-id>`.

## Approval gates

### Reading gate state

```http
GET /gates/a46614b1:designer
```

```json
{
  "gate_id": "a46614b1:designer",
  "status": "pending",
  "quorum_evaluated": true,
  "artifact_ref": "WMS-35/a46614b1/output",
  "items": [
    {"id": "mpa-a46614b1:designer-0-security-reviewer", "role": "security-reviewer",
     "scope": "security:approve", "rule_index": 0, "status": "approved", "resolved_by": "alice"},
    {"id": "mpa-a46614b1:designer-0-release-manager", "role": "release-manager",
     "scope": "security:approve", "rule_index": 0, "status": "pending", "resolved_by": ""}
  ]
}
```

**A gate id is `<run_id>:<agent_id>`, where `run_id` is the job id.** Split on the **last** colon.
(It used to be `<topology_id>:<agent_id>` inside the node, which was not unique per run — two
concurrent runs of one topology shared a gate.)

**`quorum_evaluated` is the field that matters for correctness.** It reports how `status` was
derived:

- **`true`** — the gate's `ApprovalPolicy` was reachable and the **approval engine** evaluated it
  (quorum, `min_distinct_approvers`, `exclude_author`). This is the same `evaluate()` the runtime
  gates on, so the report agrees with the decision.
- **`false`** — the policy could not be located, so the server folded the review items instead:
  *every* task must be approved. That bar is correct only for `quorum: all`.

This is why `GET /review?gate_id=…` is not a substitute: it returns the individual role-tasks, and
turning those into a decision means reading a funnel a client cannot see.

### Resolving a role-task

A multi-party gate fans out into one review item per (rule, role). Resolve them individually:

```http
POST /review/{item_id}/resolve
{"outcome": "approve", "comment": "ships"}
```

**The body carries no identity.** The resolver is the *authenticated caller*
(`request.state.identity.client_id`); a body-supplied `identity` is ignored.

Three things must hold or the call 403s, with the reason in `detail`:

1. The caller holds `approvals:resolve` — a **reserved human-identity scope**. A transport
   (API-key / JWT) token structurally cannot carry it, so **an agent or webhook integration can
   never resolve an approval gate.** Quorum a service account can satisfy is not quorum.
2. The item is a multi-party role-task (`kind: "role_task"`).
3. The caller is a member of that role in the workspace role registry, and the role confers the
   scope.

`outcome` is `approve`, `reject`, or `changes_requested`; the comment reaches the agent — a parked
run resumes with it, a re-run reads it as *why* it is running again. Only decisions about the
**current** artifact count toward quorum; earlier rounds stay on the record, marked stale.

Every attempt is audited as `approval.role_task_resolved`, allowed or denied.

**Serve `client_id` and role `members` are one identity namespace.** An operator in role
`security-reviewer` must authenticate as the identity listed in that role's `members`. A typo there
surfaces as a 403 at resolve time, not at workspace validation — the runtime cannot enumerate an
auth provider's credentials.

Under the default `NoneAuthProvider` every caller is `anonymous`, so multi-party approval is not
enforceable. It works only if the workspace genuinely lists `anonymous` as a role member, which is a
local-development convenience and not a deployment posture.

`GET /whoami` returns the authenticated caller, for a front-end that needs to say which capacity it
is acting in. `/auth-info` is the *public* endpoint and describes the server's auth mode, not the
caller.

### Listing what is waiting

```http
GET /review?kind=role_task&gate_id=a46614b1:designer
```

`kind` is one of `permission` (a harness permission), `input` (a harness question), `role_task`
(multi-party approval), or `other`. A role-task carries `gate_id`, `role`, `scope`, `rule_index`,
`resolved_by` and `artifact_ref`.

Do not use `POST /review/{id}/approve|reject` on a role-task: those record no identity and cannot
satisfy a multi-party rule.

### Reading the artifact under review

```http
GET /artifacts/WMS-35/a46614b1/output
```

An approver deciding without the artifact is deciding on a title. The ref is
`<correlation>/<run>/<name>`; a review item carries the one it is about.

## Inbound events

`POST /events/signal` is the surviving ingress seam: a signed webhook lands on `swarmkit serve`,
the signature is validated, an opaque `correlation_id` is extracted from the body via JSONPath, and
the event is handed to whatever your application registered. SwarmKit does not decide what an event
*means* — that was the sequencer's job, and the sequencer is yours now.

A trigger whose `credentials_ref` names an environment variable that is not present **refuses to
start**. Accepting unsigned requests because the secret is missing is a fail-open, and the previous
behaviour — warn and skip validation — was indistinguishable at runtime from a correctly configured
trigger. Note that `swarmkit serve` does not load a `.env` file; export the variable or source it in
the shell that starts it.

## Checking the workspace before you drive it

Two read-only reports, both from one compile, that answer questions a client otherwise finds out at
run time:

```http
GET /workspace/reachability     # configuration no code path can reach
GET /workspace/verification     # which topology roots produce an output nothing checks
```

The CLI equivalents gate CI: `swarmkit validate --require` and `swarmkit validate --require-verified`.

## Diagnosing a failed run

A harness node that dies without emitting its terminal `result` event reports the exit code and the
tail of its stderr, carried on `ExecResult.exit_metadata` and recorded in the `executor.result`
audit payload:

```
[harness:claude-code] failure: no result event (exit 1): error: unknown flag --foo
```

The tail is bounded and logged at `debug`, since a harness can print credentials.

`GET /jobs/{id}` merges the live job with its durable row, so a field the database can answer is
never reported absent because a lighter in-memory object answered first — the bug that made a
persisted 20,997-character diff read as `null`.

## Storage

Configure it once, in `workspace.yaml`:

```yaml
storage:
  runtime:
    backend: postgres
    url: postgresql://user:pass@host/db     # libpq form, not postgresql+psycopg://
```

`SWARMKIT_STORE_BACKEND` / `SWARMKIT_STORE_URL` override it. A `backend` naming a real database with
no resolvable URL **fails at startup** — degrading silently to SQLite would mean writing a run to a
different database than the one configured.

The resolved backend and its source are logged at startup:

```
Store backend: postgres (source: workspace.yaml)
```

If your application keeps its own sequencing state, keep it in **your** database. SwarmKit's store
holds runs, audit, artifacts, memory, fleet and checkpoints — not your workflow.

## See also

- [Extracting the pipeline](../design-notes/extracting-the-pipeline.md) — why sequencing left, and what replaced it.
- [Reading a gate, and approving without a saga](../design-notes/gate-state-and-deferring-approval.md) — the gate read + defer/resume design.
- [Approval policy](approval-policy.md) — quorum, roles, and who may resolve.
- [Serve](serve.md) — the full HTTP surface.
- [CLI](cli.md) — `swarmkit run`, `swarmkit review`.
- [`examples/pipeline-orchestrator/`](https://github.com/delivstat/swarmkit/tree/main/examples/pipeline-orchestrator) — the reference application.
