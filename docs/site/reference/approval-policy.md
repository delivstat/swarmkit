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

## See also

- [Multi-party approval design note](https://github.com/delivstat/swarmkit/blob/main/design/details/multi-party-approval.md) — the authoritative resolution, quorum, overlap, and audit model.
- [Role registry](role-registry.md) — where the roles and their scopes are defined.
- [Funnel](funnel.md) — the artifact whose required `approve` layer *is* an approval policy.
