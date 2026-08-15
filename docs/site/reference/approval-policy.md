# Approval policy

An **approval policy** is the per-gate, multi-party human-approval configuration: the rules that must **all** be satisfied for a gate to advance, plus segregation-of-duties controls. It is **embedded config, not a standalone artifact** — it has no `apiVersion`/`kind` and appears inside a gate (most commonly the required `approve` layer of a [Funnel](funnel.md)). Its `roles` resolve against the workspace [role registry](role-registry.md).

The resolution model, quorum semantics, four-eyes floor, and enforcement are specified in the [multi-party approval design note](https://github.com/delivstat/swarmkit/blob/main/design/details/multi-party-approval.md). This page is the config reference.

## Fields

Required: `rules` (at least one). Defaults below are applied by the runtime.

| Field | Required | Default | What it does |
|---|---|---|---|
| `rules` | yes | — | The approval rules. **Every** rule must be satisfied for the gate to advance. |
| `exclude_author` | no | `true` | The identity that authored/submitted the artifact cannot approve it (segregation of duties). |
| `on_revision` | no | `reset_all` | What a revision does to prior approvals: `reset_all` invalidates all; `reconfirm_changed` keeps approvals whose scope was unaffected. |
| `min_distinct_approvers` | no | — | Four-eyes floor: at least N **distinct** human identities must approve across all completed role-tasks, regardless of how roles overlap. |

### Rule fields

| Field | Required | What it does |
|---|---|---|
| `scope` | yes | The authority exercised (`<resource>:<action>`, e.g. `design:approve`). Every role in `roles` must confer it (validated against the role registry at load time). |
| `roles` | yes | The group of roles that may exercise this rule's scope (at least one). |
| `quorum` | yes | `all` (every role in the group approves) \| `any` (one suffices) \| `{ k-of: N }` (any N distinct role-holders). |

**Two independent axes.** *Which roles signed* is the quorum (`all`/`any`/`k-of`); *how many independent people signed* is `min_distinct_approvers`. A single dual-hatted person can complete two role-tasks and satisfy two roles, but does **not** satisfy `min_distinct_approvers: 2` — a second identity is still required.

## Config shape

```yaml
approve:                       # e.g. a Funnel's approve layer
  rules:                       # every rule must be satisfied
    - scope: design:approve
      roles: [oms-lead, web-lead, mobile-lead]
      quorum: all              # all | any | { k-of: N }
    - scope: security:approve
      roles: [infosec-lead]
      quorum: all
  exclude_author: true         # default true — segregation of duties
  on_revision: reset_all       # default reset_all | reconfirm_changed
  min_distinct_approvers: 2    # optional four-eyes floor
```

## How it resolves

A gate fans out into **one task per required role** (`Approval from role:<name>`), assigned to that role's members; a role-task completes when any one member approves. A person holding two required roles gets **two** tasks and completes each separately — one attributable sign-off per capacity. The gate compiles to a checkpointed `interrupt()`, so a partially-approved gate ("2 of 4, waiting on infosec + cio") is durable across weeks and restarts. None of this is promptable or agent-reachable.

## Who may resolve a role-task

The resolver's identity is **load-bearing** — it is what quorum, `min_distinct_approvers` and
`exclude_author` are counted against — so it is never self-asserted:

| Surface | Resolver identity | Command |
| --- | --- | --- |
| `swarmkit serve` HTTP | the authenticated caller (`client_id`) | `POST /review/{id}/resolve` with `{"outcome": "approve"}` |
| CLI | asserted via `--as` (local filesystem trust) | `swarmkit review resolve <id> --as alice --approve` |

Over HTTP the caller must also hold `approvals:resolve`, a **reserved human-identity scope**: a
transport (API-key / JWT) token structurally cannot carry it, so an agent or webhook can never cast
a resolution regardless of its serve tier. A request body may not supply an identity; one that tries
is ignored.

Membership is checked **before** the resolution is recorded, and a non-member is refused with the
reason (`alice is not a member of role release-manager`) rather than silently ignored. Every
attempt — allowed or denied — is written to the append-only audit as `approval.role_task_resolved`.

Two deployer-facing consequences:

- **Serve `client_id` and role `members` are one namespace.** An operator in role
  `security-reviewer` must authenticate as the identity listed in that role's `members`.
- **A typo in `members` surfaces at resolve time**, as a 403, not at workspace validation — the
  runtime cannot enumerate an auth provider's credentials.

Under the default `NoneAuthProvider` every caller is `anonymous`, so multi-party approval is not
enforceable unless the workspace genuinely lists `anonymous` as a role member (which keeps local
development workable, and is not a deployment posture).

`swarmkit review approve|reject` are for harness gates and do **not** record an identity — they
cannot satisfy a multi-party rule. Use `resolve`.

## Reading a gate's state

Role-tasks serialize as `kind: "role_task"` carrying `gate_id`, `role`, `scope`, `rule_index` and
`resolved_by`, so a front-end can group a gate's tasks and show which capacity each approver is
acting in. Narrow the queue with `GET /review?kind=role_task&gate_id=<id>`, or
`swarmkit review list --kind role_task --gate <id>`.

`GET /gates/{gate_id}` returns the aggregate plus per-role `items` (the gate id is `<run_id>:<agent_id>` — split on the last colon).
Its `status` is evaluated through the **approval engine** whenever the gate's policy is reachable
from the workspace (`quorum_evaluated: true`) — so the report matches the decision the runtime
gates on. When the policy cannot be located (an externally-driven gate, or a renamed agent) it
falls back to folding the items, which treats *every* task approving as the bar, and reports
`quorum_evaluated: false` so a caller knows which answer it got. The two differ for any quorum
other than `all`: under `quorum: any` the engine approves on the first resolution while the fold
still says pending.

## See also

- [Multi-party approval design note](https://github.com/delivstat/swarmkit/blob/main/design/details/multi-party-approval.md) — the authoritative resolution, quorum, overlap, and audit model.
- [Pipeline gate approval note](https://github.com/delivstat/swarmkit/blob/main/design/details/pipeline-gate-approval-ui.md) — the identity model above, and where a parked run is approved.
- [Role registry](role-registry.md) — where the roles and their scopes are defined.
- [Funnel](funnel.md) — the artifact whose required `approve` layer *is* an approval policy.
