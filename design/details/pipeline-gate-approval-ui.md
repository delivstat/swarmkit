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

## The problem: parked runs are unblockable

When a stage's funnel reaches its `approve` layer, `open_gate`
(`review/_multiparty.py:57`) fans the gate's `ApprovalPolicy` out into one `ReviewItem` per
role-task — `skill_id="multi-party-approval"`, `output.gate_id = f"{correlation_id}:{agent_id}"` —
and `resolve_multiparty` polls the queue until `evaluate()` returns APPROVED or REJECTED. The saga
moves to `status="parked"` with `pending_gate_stage` set. All of that works.

Nothing can resolve those items:

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
- **Timeout semantics.** `resolve_multiparty` degrades to a denial on timeout so a run never hangs.
  With a real approval surface, silently rejecting a run because nobody was looking at the inbox is
  arguably worse than parking indefinitely. Should a gate with a live surface park until acted on,
  with the timeout reserved for headless runs? Needs a decision before this ships, because the
  surface changes what the right default is.
- **Notification.** Nothing tells a human a gate is waiting. `notifications/` exists; wiring
  `approval.gate_opened` to it is out of scope here but is the obvious follow-on — an inbox nobody
  is told to check has the same failure mode as no inbox.
