# Driving SwarmKit from an external orchestrator

The contract between `swarmkit serve` and whatever drives it — the bundled `swarmkit orchestrator`,
a Temporal worker, or your own application. Everything here is HTTP; the runtime holds no
orchestration state of its own.

If you only read one section, read [Approval gates](#approval-gates) — that surface changed in
runtime 1.124/1.125 and a client that assumed the old shape will misreport gate state.

## The three endpoints

| Endpoint | Purpose |
| --- | --- |
| `POST /pipelines/run-stage` | Run one bounded stage; returns a `StageOutcome` |
| `POST /pipelines/signal` | Deliver one pipeline event (`emit`, or the operator acts `advance` / `skip`) |
| `GET /pipelines/gate-status/{correlation_id}/{gate}` | Learn whether a human gate has resolved |

`correlation_id` is an **opaque run handle**. Do not pass a business identifier: it also keys the
LangGraph checkpoint thread, so a reused id inherits the previous run's thread state.

### Running a stage

```http
POST /pipelines/run-stage
{"correlation_id": "run-42", "stage": {"id": "triage", "topology": "wms-triage",
                                       "gate": "triage-review", "success": "ticket.triaged"}}
```

```json
{"status": "parked", "artifact": "run-42/triage/output", "detail": ""}
```

`status` is `completed`, `parked` or `failed`. **`parked` means the stage produced its artifact and
is waiting on its human gate** — it does *not* mean the stage succeeded in any deeper sense, and a
stage that produced a useless artifact parks identically to one that produced a good one. Check the
artifact, not just the status.

The stage's input is resolved **server-side** from the saga: the pipeline `input` for the first
stage, the prior stage's artifact thereafter. See [Stage input](#stage-input) for the precedence and
for what happens when there is none.

### Signalling

```http
POST /pipelines/signal
{"correlation_id": "run-42", "event": "ticket.created", "mode": "emit"}
```

`mode: emit` needs the ordinary `serve:run` tier. `advance` and `skip` are **operator acts** gated on
the reserved scopes `pipeline:advance` / `pipeline:skip`, which a transport token structurally
cannot carry — they require a human identity. Every ingress attempt, allowed or denied, is audited
as `pipeline.ingress`.

**A parked saga drops repeat events.** Re-emitting a start event for a correlation already parked at
a gate produces no stage run and no error. Resolve or clear the gate first.

## Approval gates

### Reading gate state

```http
GET /pipelines/gate-status/run-42/triage
```

```json
{
  "correlation_id": "run-42",
  "gate": "triage",
  "status": "pending",
  "items": [
    {"id": "mpa-run-42:triage-0-security-reviewer", "role": "security-reviewer",
     "scope": "security:approve", "rule_index": 0, "status": "approved", "resolved_by": "alice"},
    {"id": "mpa-run-42:triage-0-release-manager", "role": "release-manager",
     "scope": "security:approve", "rule_index": 0, "status": "pending", "resolved_by": ""}
  ],
  "quorum_evaluated": true
}
```

`items` and `quorum_evaluated` were **added in runtime 1.125.0**; both are additive, so a client that
ignores them keeps working. `status` still means what it did.

**`quorum_evaluated` is the field that matters for correctness.** It reports how `status` was
derived:

- **`true`** — the gate's `ApprovalPolicy` was reachable and the **approval engine** evaluated it.
  This is the same `evaluate()` the runtime itself gates on, so the report agrees with the decision.
- **`false`** — the policy could not be located (an externally-driven gate, or a since-renamed
  agent), so the server folded the review items instead: *every* task must be approved. That bar is
  correct only for `quorum: all`.

Before 1.125.0 the fold was the only behaviour and there was no flag. **If you poll this endpoint
against a gate whose quorum is `any` or `k-of-n`, an older runtime reports `pending` after the gate
has already opened, and your orchestrator waits forever.** If you must support both, treat
`quorum_evaluated: false` as advisory and confirm against `items`.

### Resolving a role-task

A multi-party gate fans out into one review item per (rule, role). Resolve them individually:

```http
POST /review/{item_id}/resolve
{"outcome": "approve"}
```

**The body carries no identity.** As of runtime 1.124.0 the resolver is the *authenticated caller*
(`request.state.identity.client_id`); a body-supplied `identity` is ignored. This is a **breaking
change** for any client built against the earlier shape — it previously took
`{"identity": "...", "outcome": "..."}` and trusted it.

Three things must hold or the call 403s, with the reason in `detail`:

1. The caller holds `approvals:resolve` — a **reserved human-identity scope**. A transport
   (API-key / JWT) token structurally cannot carry it, so **an agent or webhook integration can
   never resolve an approval gate.** This is deliberate: quorum that a service account can satisfy
   is not quorum.
2. The item is a multi-party role-task (`kind: "role_task"`).
3. The caller is a member of that role in the workspace role registry, and the role confers the
   scope.

Every attempt is audited as `approval.role_task_resolved`, allowed or denied.

**Serve `client_id` and role `members` are one identity namespace.** An operator in role
`security-reviewer` must authenticate as the identity listed in that role's `members`. A typo there
surfaces as a 403 at resolve time, not at workspace validation — the runtime cannot enumerate an
auth provider's credentials.

Under the default `NoneAuthProvider` every caller is `anonymous`, so multi-party approval is not
enforceable. It works only if the workspace genuinely lists `anonymous` as a role member, which is a
local-development convenience and not a deployment posture.

`GET /whoami` (added 1.126.0) returns the authenticated caller, for a front-end that needs to say
which capacity it is acting in. `/auth-info` is the *public* endpoint and describes the server's
auth mode, not the caller.

### Listing what is waiting

```http
GET /review?kind=role_task&gate_id=run-42:triage
```

`kind` is one of `permission` (a §6.2 harness permission), `input` (a §6.3 harness question),
`role_task` (multi-party approval), or `other`. A role-task carries `gate_id`, `role`, `scope`,
`rule_index` and `resolved_by` — before 1.125.0 it serialized as `kind: "other"` with those fields
dropped, which is why an older client cannot render one.

A gate id is `<correlation_id>:<agent_id>`. Split on the **last** colon.

Do not use `POST /review/{id}/approve|reject` on a role-task: those record no identity and cannot
satisfy a multi-party rule.

## Stage input

`_stage_input` resolves, in order:

1. `stage["input"]`, when the caller supplied one on the run-stage request.
2. The saga's `input` — the payload carried on the `start` event — for the first stage of a run.
3. The prior stage's artifact, for every later stage.

**A stage with no resolvable input fails** rather than running. Earlier runtimes fell back to the
agent's *role name* as the prompt, which meant a stage that received nothing still called the model,
returned `completed`, and wrote a plausible artifact — indistinguishable from a stage that did its
job. If you are debugging "the endpoint looks healthy but the output is wrong", check that a saga
exists for the correlation id: a replayed request against a correlation with no saga has no input.

## Storage

Serve and the orchestrator **must agree on the store.** They communicate only through the durable
saga store plus this HTTP seam, so a mismatch is a split brain: serve queues events into one
database while the orchestrator polls another. Neither process errors; the webhook returns 200, the
saga is created, and no stage ever runs.

Configure it once, in `workspace.yaml`:

```yaml
storage:
  runtime:
    backend: postgres
    url: postgresql://user:pass@host/db     # libpq form, not postgresql+psycopg://
```

`SWARMKIT_STORE_BACKEND` / `SWARMKIT_STORE_URL` override it. `swarmkit orchestrator` resolves the
same config through the same precedence; `--database-url` is an explicit override and is the only
supported way to point the two at different stores.

A `backend` naming a real database with no resolvable URL **fails at startup**. Degrading silently
to SQLite would mean writing a run to a different database than the one configured.

The resolved backend and its source are logged at startup:

```
Store backend: postgres (source: workspace.yaml)
```

## Diagnosing a failed stage

A harness stage that dies without emitting its terminal `result` event reports the exit code and the
tail of its stderr, carried on `ExecResult.exit_metadata` and recorded in the `executor.result`
audit payload:

```
[harness:claude-code] failure: no result event (exit 1): error: unknown flag --foo
```

The tail is bounded and logged at `debug`, since a harness can print credentials.

## Webhook triggers

A trigger whose `credentials_ref` names an environment variable that is not present **refuses to
start**. Accepting unsigned requests because the secret is missing is a fail-open, and the previous
behaviour — log a warning and skip signature validation — was indistinguishable at runtime from a
correctly configured trigger. Note that neither `swarmkit serve` nor `swarmkit orchestrator` loads a
`.env` file; export the variable or source it in the shell that starts them.

## See also

- [Approval policy](approval-policy.md) — quorum, roles, and who may resolve.
- [CLI](cli.md) — `swarmkit pipeline`, `swarmkit review`.
- [Bundled pipeline orchestrator](https://github.com/delivstat/swarmkit/blob/main/design/details/bundled-pipeline-orchestrator.md)
- [Pipeline gate approval](https://github.com/delivstat/swarmkit/blob/main/design/details/pipeline-gate-approval-ui.md)
