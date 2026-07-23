# Role registry

A **role registry** is a first-class SwarmKit artifact (`kind: RoleRegistry`) that maps each governance **role** to the human identities that hold it and the governance **scopes** it confers. It is workspace-level IAM data: the single place a person's authority is recorded, so a handover is one membership edit. Multi-party [approval policies](approval-policy.md) — including the `approve` layer of a [Funnel](funnel.md) — resolve their `roles` against this registry.

The RBAC model, registry-driven reserved scopes, and gate resolution are specified in the [multi-party approval design note](https://github.com/delivstat/swarmkit/blob/main/design/details/multi-party-approval.md). This page is the artifact reference.

## What a role registry is for

Real approvals are plural and role-based: a design needs every app lead *and* InfoSec; a release needs the engineering manager *and* the CIO. A role carries **many scopes** (identity → role → scopes), so membership lives in one place per person and cannot silently drift. People join and leave independently of any topology, so the registry versions like any other artifact.

**Registry-driven reserved scopes.** Any scope conferred by any role is a *human-identity scope*: the policy engine refuses to grant it to a non-human (agent) principal — the same structural mechanism as the built-in reserved scopes (`skills:activate`, `iam:modify`, …), but driven by the registry rather than a hardcoded list. No agent can hold or satisfy an approval scope, regardless of prompt.

## Role fields

Required top-level: `apiVersion`, `kind`, `metadata`, `roles` (at least one).

| Field (per role) | Required | What it does |
|---|---|---|
| `id` | yes | Lowercase-kebab role id, unique in the workspace (enforced at load time). |
| `members` | yes | Human identity references that hold this role. May be **empty** for an unstaffed role — but a gate requiring an unstaffed role cannot reach quorum. |
| `scopes` | yes | Governance scopes this role confers, each of the form `<resource>:<action>` (e.g. `design:approve`). At least one. |

## Schema shape

```yaml
apiVersion: swarmkit/v1
kind: RoleRegistry
metadata:
  id: <lowercase-kebab>
  name: <human name>
  description: <optional>
roles:                             # at least one; ids unique
  - id: <role id>
    members: [<identity>, ...]      # may be empty (unstaffed -> cannot reach quorum)
    scopes: [<resource:action>, ...]  # at least one
```

## Example

```yaml
apiVersion: swarmkit/v1
kind: RoleRegistry
metadata:
  id: sdlc-roles
  name: SDLC Approval Roles
roles:
  - id: oms-lead
    members: [alice]
    scopes: [design:approve, code:approve, deploy:approve]
  - id: web-lead
    members: [bob]
    scopes: [design:approve, code:approve]
  - id: infosec-lead
    members: [dana]
    scopes: [security:approve]
  - id: cio
    members: [heidi]
    scopes: [release:approve]
```

An [approval policy](approval-policy.md) (or a Funnel `approve` block) then references these roles, and validation checks that every role named in a rule actually confers that rule's scope.

## Authoring a role registry

`get_schema("role-registry")` returns the exact shape. A role's `scopes` are what let a rule name it (a rule referencing a role that does not confer the rule's scope is rejected at load time); membership is per-identity, so keep one role per capacity rather than duplicating people across roles when four-eyes independence matters.

## See also

- [Multi-party approval design note](https://github.com/delivstat/swarmkit/blob/main/design/details/multi-party-approval.md) — RBAC model, reserved scopes, task decomposition, and audit.
- [Approval policy](approval-policy.md) — the per-gate rules that resolve against this registry.
- [Funnel](funnel.md) — its `approve` layer is a multi-party approval policy over these roles.
