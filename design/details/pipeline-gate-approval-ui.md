---
title: Resolving a parked pipeline's approval gate (identity + surfaces)
description: A pipeline saga parks on a funnel's multi-party approval gate, but nothing in the product can resolve it — the review items serialize as `kind: "other"`, no client calls `POST /review/{id}/resolve`, and the CLI has no `resolve` verb. This note fixes the identity model first (the resolver is the authenticated caller, not a request-body string), then adds the run-scoped approval surface in `/runs`, the inbox entry in `/gates`, and CLI parity.
tags: [runtime, serve, ui, cli, governance, pipeline, approval]
status: draft
---

# Resolving a parked pipeline's approval gate

**Scope:** `runtime` (serve review/pipeline routes), `ui` (`/runs`, `/gates`), `cli` (`swarmkit review`)
**Design reference:** §8.5 (GovernanceProvider), §8.7 (reserved-for-human scopes), §14 (runtime/serve).
Builds on `multi-party-approval.md` (the approval engine), `gate-funnel.md` (where a gate opens), and
`bundled-pipeline-orchestrator.md` (the saga that parks).
**Status:** draft

## Goal

Make a parked pipeline run resolvable by a human through the product — from the run view, from the
CLI, with the resolver's identity taken from their authenticated session rather than asserted in a
request body.

## Non-goals

- **Not changing the approval engine.** `ApprovalPolicy`, `RoleRegistry`, `evaluate`,
  `collect_resolutions` and the quorum model are unchanged. This note is a surface + identity note.
- **Not a new approval mechanism.** Funnel gates keep using the one review queue that the CLI,
  serve UI and fleet UI already share. No parallel queue, no pipeline-specific approval store.
- **Not an operator override.** Releasing a parked saga by fiat stays `swarmkit pipeline advance`,
  gated on the reserved `pipeline:advance` scope. Resolving a gate is not a way to skip it.
- **Not notification delivery.** Telling a human a gate is waiting is a follow-on (see Open
  questions).
- **Not a control-plane change.** The fleet panel stays an independent client; nothing here adds a
  runtime dependency to it.

## Two parking mechanisms, and only one of them has an approval policy

"The run is parked" means two different things depending on which path produced it, and the
distinction governs everything below.

**Path A — the stage-graph gate (what `swarmkit serve` + the bundled orchestrator actually run).**
`build_pipeline_run_stage` (`server/_pipeline_stage.py:81`) runs the stage's topology, stores the
artifact, and returns `StageOutcome(status="parked")` whenever `stage.gate` or `stage.funnel` is
set. The saga persists `status="parked"` and `pending_gate_stage`. This is durable and correct —
nothing is held in memory, a restart loses nothing. But it **never calls `open_gate`**: no
`ApprovalPolicy` is evaluated, no role-tasks are created, no quorum is enforced. The only way to
release it is an operator emitting the `gate` event (`swarmkit pipeline advance`, reserved scope
`pipeline:advance`). Consequently `GET /pipelines/gate-status/{cid}/{gate}` folds an empty item
list and reports `pending` forever (`_aggregate_gate_status` returns `pending` for `not items`) —
correct as written, but it means the route reports on a mechanism this path does not use.

**Path B — the agent funnel's `approve` layer.** `StageRunner._run_gated_stage`
(`langgraph_compiler/_stage_runner.py:122`) calls `run_agent_funnel_gate` with
`gate_id=f"{correlation_id}:{agent.id}"`, which reaches `build_multiparty_approver` and
`resolve_multiparty`. *This* is where `open_gate` (`review/_multiparty.py:57`) fans the
`ApprovalPolicy` into one `ReviewItem` per role-task and polls until `evaluate()` returns APPROVED
or REJECTED. Real multi-party semantics — and a blocking `await` inside the running topology, with
`max_wait_seconds` degrading to a denial. `StageRunner` is currently wired only in
`examples/sdlc-pipeline` and tests; the bundled serve path does not construct it.

So the product today offers durable parking without an approval policy (A), or a real approval
policy that cannot survive a restart (B), and no path that is both. The surface work below is
necessary but not sufficient: it makes B's items resolvable. Making the *bundled* pipeline enforce
multi-party approval means giving A a policy, which the decision in "Parking is the default"
resolves.

## The problem: parked runs are unblockable

Taking path B on its own terms — the only path that opens gate items at all — nothing can resolve
those items:

1. **They are invisible to the frontend.** `_item_to_dict` (`server/_routes_review.py:31`) maps
   `skill_id` to one of three kinds — `permission`, `input`, `other` — and multi-party items fall
   to `other`. The `/gates` page (`packages/ui/app/gates/page.tsx`) branches only on `permission`
   and `input`, so a parked gate renders as a card with an empty body. The serializer also drops
   `gate_id`, `scope`, `role` and `rule_index`, so even a UI that added a branch could not group
   items by gate or tell the approver which role they are acting as.
2. **No client calls the resolve route.** `POST /review/{item_id}/resolve` exists and works;
   `packages/ui/lib/api.ts` has `reviewApprove` / `reviewReject` / `reviewAnswer` and no
   `reviewResolve`. `swarmkit review` (`cli/_cmd_authoring.py:99-187`) has `list`, `show`,
   `approve`, `reject`, `answer` — no `resolve`.
3. **`approve`/`reject` are the wrong verbs for these items.** They set a single item's status
   without recording *who* resolved it. `collect_resolutions` needs a `Resolution(identity, role,
   scope, outcome)`; an item approved via the harness-gate path carries no identity, so it cannot
   count toward quorum.

Net effect: a parked pipeline can be unblocked only by hand-rolling an HTTP request, or by waiting
out `_DEFAULT_MAX_WAIT_SECONDS`, at which point `resolve_multiparty` degrades the gate to a denial
and the run is rejected. The governance machinery is sound; the surface over it is missing.

## The identity problem, which comes first

`ResolveRequest` currently takes `identity: str` from the request body. That is the load-bearing
defect, and every surface below inherits it.

`evaluate(policy, registry, resolutions, author)` enforces the multi-party guarantee through
`resolution_error` (`governance/_approval.py:174`): the role must confer the scope, the identity
must be a **member of that role** per the workspace `RoleRegistry`, and under `exclude_author` the
identity must not be the artifact's author. A body-supplied identity makes all three checks
self-asserted. One operator with a `serve:run` token can satisfy a 3-of-3 policy by resolving each
role-task under a different name, and the audit records the names they typed.

The fix has precedent in this codebase. `POST /pipelines/signal` already derives its actor from the
session — `actor = request.state.identity.client_id` — and `_ingress_pipeline_event`
(`server/_routes_pipelines.py:108`) authorizes operator modes against reserved scopes
(`pipeline:advance` / `pipeline:skip`), which `auth/_scopes.py` structurally forbids a transport
token from carrying (§8.7). Resolving a multi-party gate is the same category of act. It follows
the same shape:

- **Resolver identity is `request.state.identity.client_id`.** `ResolveRequest.identity` is removed.
  The body carries `outcome` only.
- **A new reserved scope, `approvals:resolve`,** joins `RESERVED_SCOPES` and is checked via
  `governance.evaluate_action` before the queue is touched — so an agent or webhook token can never
  resolve a gate, whatever its tier.
- **The attempt is audited either way,** allowed or denied, as `approval.role_task_resolved` with
  `gate_id`, `item_id`, `role`, `scope`, `outcome`, `identity` — mirroring `pipeline.ingress`.

This makes the serve `client_id` namespace and the `RoleRegistry` member namespace the same
namespace. That is a real constraint on deployers and must be documented: an operator who is a
member of role `security-reviewer` must authenticate as the identity listed in that role's
`members`. Where they diverge, resolution fails closed with `"{identity} is not a member of role
{role}"` — the existing `resolution_error` message, surfaced to the caller as a 403 rather than
silently ignored (today invalid resolutions are dropped by `evaluate`, which would leave the
approver staring at a gate that never advances).

`NoneAuthProvider` yields an anonymous identity. In that configuration multi-party approval is not
enforceable, and the resolve route should 403 with that reason rather than pretend. Single-role
`quorum: any` policies over a role whose members include the anonymous identity still work, which
keeps local development usable without weakening the deployed case.

## Parking is the default; the timeout is for headless runs

**Decision:** a gate parks until a human acts on it. The timeout-degrades-to-denial behaviour is
retained only for runs with nobody attached.

The current default is backwards. `resolve_multiparty` degrades to a denial on timeout so that a run
never hangs — defensible when no approval surface exists, because an indefinitely blocked run is
invisible and a denial at least terminates. Once there is a surface, that default *rejects work
because nobody looked at an inbox*, which is a worse and much quieter failure: the run is marked
rejected, the artifact is discarded, and the audit says the gate was denied when in fact it was
never seen.

**Attendedness is declared, not detected.** There is no reliable signal for "a human is watching" —
an open browser tab is not a commitment, and sniffing whether serve is running would make gate
semantics depend on deployment topology. So it goes in the artifact, where the rest of the funnel
policy already lives:

```yaml
approve:
  rules: [...]
  on_timeout: park        # park (default) | deny
  timeout: 24h            # only meaningful with on_timeout: deny
```

`park` is the default. A headless caller that must terminate — CI, cron, an eval harness — sets
`on_timeout: deny` with a deadline, or passes `--gate-timeout` at the CLI, which overrides the
artifact. Unattended runs stay bounded; attended runs stop discarding work.

**The implementation consequence is the real cost, and it is not a config flag.** Path B parks by
holding a coroutine in `resolve_multiparty`'s poll loop. `max_wait_seconds=None` does not implement
"park until acted on" — it implements "leak a coroutine until the process restarts, then lose the
gate entirely." Durable parking means path B must do what path A already does: **return** a parked
outcome and let the saga persist it, resuming when the gate resolves. Concretely:

- `run_agent_funnel_gate` gains a non-blocking mode: `open_gate`, then return an outcome of
  `parked` with the `gate_id`, rather than polling.
- `StageRunner._run_gated_stage` propagates that instead of awaiting a decision.
- `build_pipeline_run_stage` opens the stage's `ApprovalPolicy` before returning `parked` — which
  is also what gives **path A a policy** and makes `GET /pipelines/gate-status` report on something
  real.
- Resolution becomes the *trigger*: the last role-task resolving emits the `gate` event the
  controller already resumes on, so `swarmkit pipeline advance` reverts to what it should always
  have been — a break-glass override, not the normal path.

This converges A and B on one mechanism. It is a larger change than the surface work, and it is the
reason the CLI `resolve` verb ships first: `resolve` against path B's blocking implementation is
useful on day one and does not have to wait for the convergence.

## Where approval happens

**Primary: `/runs`, in the node inspector on the parked stage.** The run detail already defaults the
inspector to `saga.pending_gate_stage ?? saga.current_stage` (`app/runs/page.tsx:201`) and already
renders the stage's timeline, produced artifact and approval trail — read-only. This is the only
surface where the approver sees *what they are approving* before they approve it. Approving from a
context-free list is how gates get rubber-stamped, which is the behaviour `swarmkit comprehension`
exists to detect after the fact; better not to design it in.

**Secondary: `/gates`, as the inbox.** It already fetches `GET /review` workspace-wide and is the
established "pending human decisions" surface, shared with the CLI and the fleet UI. It gains a
`role_task` branch that shows gate, stage, role and scope, and **deep-links to
`/runs?run=<correlation_id>&stage=<stage>`**. It does not grow its own approve/reject buttons for
role-tasks — the inbox tells you a decision is waiting; the run view is where you make it.

**Not `/funnels`.** That surface authors funnel policy (design-time). Run-time decisions stay out of
it.

**CLI first.** Per the CLI-is-first-class rule, `swarmkit review resolve` lands before the UI work.
It is also the only surface that can be exercised end-to-end in CI.

## API shape

```python
# server/_routes_review.py — identity comes from the session, not the body.
class ResolveRequest(BaseModel):
    outcome: Literal["approve", "reject"]

@app.post("/review/{item_id}/resolve")
async def resolve_multiparty_task(
    item_id: str, body: ResolveRequest, request: Request
) -> dict[str, Any]: ...
    # 403 when the caller lacks `approvals:resolve`, is anonymous, or fails resolution_error().

# _item_to_dict gains a fourth kind + the fields a gate UI needs.
kind = "role_task" if item.skill_id == "multi-party-approval" else ...
{
    "kind": "role_task",
    "gate_id": "run-42:security-review",   # f"{correlation_id}:{agent_id}"
    "scope": "security",
    "role": "security-reviewer",
    "rule_index": 0,
    "resolved_by": "alice",                 # None while pending
    ...
}

# Per-gate detail — the aggregate fold is not enough to render N role-tasks.
@app.get("/pipelines/gate-status/{correlation_id}/{gate}")
async def gate_status(...) -> GateStatusResponse: ...
    # GateStatusResponse gains `items: list[RoleTaskSummary]` and `policy_summary: str`.
```

```typescript
// packages/ui/lib/api.ts
reviewResolve: (id: string, outcome: "approve" | "reject") =>
  post<ReviewGate>(`/review/${id}/resolve`, { outcome }),
gateStatus: (correlationId: string, gate: string) =>
  get<GateStatus>(`/pipelines/gate-status/${correlationId}/${gate}`),
```

```
swarmkit review resolve <item-id> --approve | --reject
    # identity comes from the configured serve credential, same as `swarmkit pipeline advance`
swarmkit review list --kind role_task
```

`swarmkit pipeline` gains no new verb: advancing a parked saga is a *consequence* of the gate
resolving, not an operator override. `pipeline advance` remains the separate break-glass path.

## Test plan

- **Unit (runtime).** `_item_to_dict` emits `kind: "role_task"` with gate/scope/role/rule_index.
  `resolve` route: derives identity from `request.state.identity`; 403 for a caller without
  `approvals:resolve`; 403 for anonymous under `NoneAuthProvider`; 403 with the `resolution_error`
  text when the identity is not a member of the role; 403 under `exclude_author` when the resolver
  authored the artifact; audit event written on both the allow and the deny path.
  `reserved_violations` rejects a transport token carrying `approvals:resolve`.
- **Unit (ui).** `api.reviewResolve` posts the right body. `/gates` renders a `role_task` card and
  links to the run. The `/runs` inspector renders one row per role-task with the correct
  enabled/disabled state for the current identity.
- **Integration.** A funnel whose `approve` layer carries a 2-of-2 policy: open the gate, resolve
  one role-task (gate stays PENDING, saga stays `parked`), resolve the second, assert
  `evaluate()` → APPROVED, the saga leaves `parked`, and the stage proceeds. Then the negative:
  one identity attempts both role-tasks and the second is refused.
- **Full pipeline.** Per the always-test-the-full-pipeline rule, a live `swarmkit serve` +
  `swarmkit orchestrator` run of `examples/sdlc-pipeline`, parked on a real gate and released via
  `swarmkit review resolve` — not just unit coverage.
- **Test data.** A `roles/` registry fixture with two roles and distinct members; a stage-graph
  fixture with one gated stage.

## Demo plan

`just demo-pipeline-approval` — starts serve + the bundled orchestrator against
`examples/sdlc-pipeline`, emits an event that drives the saga to a gated stage, and prints:

1. `swarmkit pipeline status <cid>` showing `parked` on `pending_gate_stage`.
2. `swarmkit review list --kind role_task` showing both role-tasks with their roles.
3. Two `swarmkit review resolve … --approve` calls under two identities; the first leaves the run
   parked, the second releases it.
4. `swarmkit pipeline status <cid>` showing the stage passed, and the audit tail showing both
   `approval.role_task_resolved` events plus `approval.gate_resolved`.

Plus a screenshot of the `/runs` inspector on the parked stage with the role-task panel, and of the
`/gates` inbox entry that links to it.

## Open questions

- **Identity namespace unification.** Making serve `client_id` the role-membership identity is the
  narrow fix. The control plane has its own identity model (`control-plane/05-identity-governance-iam.md`);
  whether these converge, or whether `RoleRegistry` should carry a provider-qualified identity
  (`api_key:alice` vs `jwt:alice@corp`), is unresolved. The narrow fix is forward-compatible with
  either — it just means an early deployer's `members` list may need rewriting later.
- ~~**Timeout semantics.**~~ **Resolved:** park until acted on; the timeout is retained only for
  headless runs, declared as `approve.on_timeout` rather than detected. See "Parking is the default"
  above. The follow-on question it opens: converging paths A and B on non-blocking parking is a
  runtime change of its own and probably wants a separate design note — this one should state the
  target and stop there.
- **Notification.** Nothing tells a human a gate is waiting. `notifications/` exists; wiring
  `approval.gate_opened` to it is out of scope here but is the obvious follow-on — an inbox nobody
  is told to check has the same failure mode as no inbox.
