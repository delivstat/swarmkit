# Multi-party approval sets (governance)

Parent: `design/details/sdlc-pipeline-example.md` (capability 1 of 5). This is a standalone,
reusable governance capability — the SDLC pipeline is its first consumer, but nothing here is
SDLC-specific.

Today a human-approval gate (design §6.2) is **one question → one resolver**. Real approvals are
plural and role-based: a design needs every app lead *and* InfoSec; a release needs the
engineering manager *and* the CIO. This note adds a **configurable multi-party approval set** — a
gate that stays open until the roles it requires have each approved, by *distinct human
identities*, per a declarative per-gate policy over a workspace role registry.

## Goal

Let any gate require a configured set of approvals — expressed as **data**, not code — and have
the policy engine enforce it structurally: distinct human identities, reserved (non-agent) scopes,
quorum, and an append-only record of who approved what and when.

## Non-goals

- **Not agent approvals.** Reserved human scopes only; no agent can hold or satisfy an approval
  scope, regardless of prompt (invariant 6, §8.7). This capability *strengthens* that line.
- **Not sequencing.** How a rejected gate routes back, and how stages advance, belongs to
  `pipeline-controller`. This note only defines the gate and its resolution outcomes.
- **Not the judge/review layers.** The automated pre-filters are `gate-funnel`; this is the final,
  binding human layer.
- **Not notification/queue delivery.** This *emits* tasks; routing them to people is
  `task-surface-and-board`.

## Where it lives

Governance only. Enforcement is in the `GovernanceProvider` / policy engine
(`packages/runtime/src/swarmkit_runtime/governance/`, §8.5) — no other package decides approvals.
The role registry is workspace-level IAM data; the approval policy is gate configuration. Both are
validated artifacts (canonical schema), not runtime flags.

## API shape

### Role registry (new IAM artifact)

A workspace-level artifact mapping each role to the human identities that hold it and the
governance scopes it confers. A role carries **many scopes** (standard RBAC: identity → role →
scopes) — a lead approves designs *and* code *and* deploys, and membership then lives in **one
place per person**, so a handover is a single edit and cannot silently drift. Versioned like any
artifact (people join/leave independently of topology).

```yaml
apiVersion: swarmkit/v1
kind: RoleRegistry
roles:
  - { id: oms-lead,     members: [alice], scopes: [design:approve, code:approve, deploy:approve] }
  - { id: infosec-lead, members: [dana],  scopes: [security:approve] }
  - { id: eng-manager,  members: [grace], scopes: [release:approve, code:approve] }
  - { id: cio,          members: [heidi], scopes: [release:approve] }
```

**Registry-driven reserved scopes.** Any scope conferred by any role is a *human-identity scope*:
the policy engine refuses to grant it to a non-human (agent) principal — the same structural
mechanism as the existing reserved scopes (`skills:activate`, `iam:modify`, …), but driven by the
registry rather than a hardcoded list (no hardcoded scope names — "everything configurable").

### Per-gate approval policy

A gate declares one or more approval **rules**, each naming the **scope** being exercised, the
group of roles that may exercise it, and a quorum mode:

```yaml
gate: consolidated-design-approval
approval:
  rules:
    - { scope: design:approve,   roles: [oms-lead, web-lead, mobile-lead], quorum: all }
    - { scope: security:approve, roles: [infosec-lead],                    quorum: all }
    - { scope: design:approve,   roles: [rev-a, rev-b, rev-c],             quorum: { k-of: 2 } }
  exclude_author: true        # segregation of duties (default true)
  on_revision: reset_all      # reset_all | reconfirm_changed
```

- `all` — every role in the group must approve.
- `any` — one role in the group suffices.
- `k-of: N` — any N **distinct** role-holders in the group.

The **rule** names its scope so a multi-scope role stays unambiguous (the role says what a person
*can* do; the rule says what is *being asked* here), and a single gate can span multiple
authorities — the example needs `design:approve` from the app leads **and** `security:approve` from
InfoSec, which neither scope-per-role nor scope-per-gate could express. Validation: every role in a
rule must confer that rule's scope. The gate advances only when **every** rule is satisfied.

### Task decomposition (one task per role)

A gate **fans out into one task per required role** — `Approval from role:<name>`, assigned to
that role's members. A role-task completes when **any one member** of the role approves (a role is
a single slot; multiple members just means anyone eligible can fill it). Quorum is counted over
**completed role-tasks**:

- `all` — every role-task in the rule completed.
- `any` — at least one role-task completed (the rest auto-close).
- `k-of: N` — any N role-tasks completed.

A person who holds **two required roles gets two tasks and completes each separately** — one
deliberate, attributable sign-off *per capacity* ("approved as `oms-lead`", "approved as
`web-lead`"), not one click standing in for two responsibilities. This is the explicit,
audit-friendly model; the small extra cost for dual-hatted people buys unambiguous accountability.

Independence (four-eyes) is a **separate axis** from task completion — see "Overlapping roles".

### Resolution model

Each resolution is one of three outcomes, authenticated to a human identity:

- `approve` — records the approval, updates the tally.
- `changes-requested` — carries free-text comments; the gate reports this outcome to the caller
  (the *rework loop itself* lives in the consuming stage — see `pipeline-controller` / the parent
  note), and prior approvals are handled per `on_revision`.
- `reject` — fails the gate.

`on_revision`: `reset_all` (default — a revised artifact invalidates prior approvals; all required
roles re-approve) or `reconfirm_changed` (only affected roles re-review; unaffected approvals
carry). Section-scoped re-approval is explicitly future work.

### Runtime: accumulation via checkpoint

The gate compiles to a **LangGraph `interrupt()` backed by the checkpointer**. The interrupt
payload is the set of *outstanding* required approvals. Each `Command(resume=…)` supplies one
identity's outcome; the gate node updates the tally, and if quorum is unmet it **re-interrupts**
with the tally persisted. So "2 of 4 approved, waiting on infosec + cio" is durable state across
weeks and across people, and a parked gate costs one DB row (no held process).

### Enforcement (structural)

The policy engine, on each resolution:
1. **Authenticates** the resolver to a human identity (not an agent).
2. Checks the identity is a member of a named role that **confers the rule's scope**; rejects
   otherwise.
3. Completes **exactly the role-task the resolution was submitted for** — a person with two role-
   tasks completes each separately; no single action covers multiple roles.
4. Enforces **`exclude_author`** — the identity that authored/submitted the artifact cannot approve
   it (segregation of duties; important for DORA/audit).
5. Refuses to advance until every rule's quorum **and** any `min_distinct_approvers` floor is met.

### Overlapping roles (one person, several hats)

A person holding two required roles gets **two tasks** and completes **both, separately** — one
attributable sign-off per capacity. Their two completions satisfy their two roles; that is the
point, not a shortcut. So task completion is purely per-role.

**Independence is a separate axis.** Because a dual-role person *can* complete two role-tasks, a
gate that also needs genuine four-eyes adds a distinct-identity floor on top:

- `min_distinct_approvers: N` at the gate level — at least N **different humans** must have approved
  across all completed role-tasks, regardless of overlap. So one dual-hatted person completing two
  role-tasks satisfies the roles but **not** a `min_distinct_approvers: 2` floor; a second identity
  is still required.

Keep the two axes distinct: **which roles signed** (task completion, `all`/`any`/`k-of`) versus
**how many independent people signed** (`min_distinct_approvers`). Overlap is most suspect when the
roles exercise *different scopes* (the same person as both design approver and independent security
sign-off) — `min_distinct_approvers`, or simply not placing one person in both roles, guards that as
an explicit policy choice, never a hardcoded heuristic.

```yaml
approval:
  rules:
    - { scope: design:approve,   roles: [oms-lead, web-lead, mobile-lead], quorum: all }
    - { scope: security:approve, roles: [infosec-lead],                    quorum: all }
  min_distinct_approvers: 2     # optional four-eyes floor across the whole gate
```

None of this is promptable or agent-reachable; it is the same class of structural gate as the
existing reserved scopes.

### Audit

Every role-task completion appends one event: `{gate_id, correlation_id, role, identity, outcome,
comment?, ts}` — so a person acting in two capacities produces **two** events, one per role, each
independently attributable. Append-only from the executive perspective (§8.3). This record *is* the
approval evidence
(who signed off, when) that DORA/compliance reporting reads — no separate spreadsheet.

## Test plan

- **Schema (Python + TS):** RoleRegistry (roles carry many scopes) and gate `approval` blocks
  validate; a rule referencing an unknown role is rejected; a role in a rule that does **not**
  confer the rule's scope is rejected; a gate spanning two scopes (design + security) validates;
  `k-of: N` with N > group size is rejected; a scope with no member (no role confers it) is rejected.
- **Quorum modes:** `all` / `any` / `k-of: N` each advance exactly at their threshold; `k-of`
  counts **distinct** identities; a duplicate approval from the same identity does not double-count.
- **Task decomposition:** a gate fans out into one task per required role; a role-task completes on
  the first approval by **any** member of that role; `all` needs every role-task, `k-of: N` needs N.
- **Overlapping roles:** a person holding two required roles gets **two** tasks and must complete
  **both** separately to satisfy both roles (one action does not cover two); with
  `min_distinct_approvers: 2` the gate is still unsatisfied after that person's two completions until
  a second distinct identity approves.
- **Reserved-scope enforcement:** an agent principal cannot be granted a registry-bound scope; an
  agent-authenticated resolution is refused.
- **Segregation of duties:** with `exclude_author: true`, the author's approval is refused; with
  `false`, it is accepted.
- **on_revision:** `reset_all` clears prior approvals on a revision; `reconfirm_changed` carries
  unaffected ones.
- **Checkpoint durability:** the running tally survives a simulated process restart (resume from
  the persisted `thread_id` continues, not restarts, the gate).
- **Audit:** each resolution appends exactly one immutable event with identity + role + outcome;
  no update/delete path is exposed.

## Demo plan

`just demo-multi-party-approval` (script under the runtime demos): a one-node topology with a
`consolidated-design-approval` gate (all three app leads + infosec, plus a `k-of: 2` reviewer
pool). The script resolves via the gates API as distinct identities and shows: the gate holding at
partial quorum, a duplicate approval rejected, an agent resolution refused, quorum reached →
advance, and the printed append-only audit trail. Terminal transcript in the PR body.

## Schema-change checklist

Adds a new artifact kind + a gate sub-schema — follow `docs/notes/schema-change-discipline.md`:
canonical JSON Schema, Python validator, TS validator, and fixtures updated together; register
`RoleRegistry` in the artifact registry alongside topologies/skills/archetypes/triggers.
