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

A workspace-level artifact mapping each role to a governance scope and the human identities that
hold it. Versioned like any artifact (people join/leave, so it changes independently of topology).

```yaml
apiVersion: swarmkit/v1
kind: RoleRegistry
roles:
  - { id: oms-lead,     scope: design:approve,   members: [alice] }
  - { id: infosec-lead, scope: security:approve, members: [dana] }
  - { id: eng-manager,  scope: release:approve,  members: [grace] }
  - { id: cio,          scope: release:approve,  members: [heidi] }
```

**Registry-driven reserved scopes.** Any scope bound in the registry is a *human-identity scope*:
the policy engine refuses to grant it to a non-human (agent) principal — the same structural
mechanism as the existing reserved scopes (`skills:activate`, `iam:modify`, …), but driven by the
registry rather than a hardcoded list (no hardcoded scope names — "everything configurable").

### Per-gate approval policy

A gate declares one or more approval **rules**, each a group of roles + a quorum mode:

```yaml
gate: consolidated-design-approval
approval:
  rules:
    - { roles: [oms-lead, web-lead, mobile-lead], quorum: all }
    - { roles: [infosec-lead],                    quorum: all }
    - { roles: [rev-a, rev-b, rev-c],             quorum: { k-of: 2 } }
  exclude_author: true        # segregation of duties (default true)
  on_revision: reset_all      # reset_all | reconfirm_changed
```

- `all` — every role in the group must approve.
- `any` — one role in the group suffices.
- `k-of: N` — any N **distinct** role-holders in the group.

The gate advances only when **every** rule is satisfied. Modelling groups explicitly (not
per-role quorum) is what makes `k-of` well-defined.

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
2. Checks the identity **holds the role's scope** via the registry; rejects otherwise.
3. Enforces **distinct identities** — one person holding two roles counts once per role but cannot
   alone satisfy a multi-identity rule (e.g. `k-of: 2`).
4. Enforces **`exclude_author`** — the identity that authored/submitted the artifact cannot approve
   it (segregation of duties; important for DORA/audit).
5. Refuses to advance until every rule's quorum is met.

None of this is promptable or agent-reachable; it is the same class of structural gate as the
existing reserved scopes.

### Audit

Every resolution appends one event: `{gate_id, correlation_id, role, identity, outcome, comment?,
ts}`. Append-only from the executive perspective (§8.3). This record *is* the approval evidence
(who signed off, when) that DORA/compliance reporting reads — no separate spreadsheet.

## Eject

The approval set ejects as a LangGraph interrupt node whose reducer accumulates approvals into
state and whose condition gates the outgoing edge on quorum — expressible in generated code, so
invariant 7 holds. The role registry + policy eject as the node's static config.

## Test plan

- **Schema (Python + TS):** RoleRegistry and gate `approval` blocks validate; a rule referencing
  an unknown role is rejected; `k-of: N` with N > group size is rejected; a scope with no members
  is rejected.
- **Quorum modes:** `all` / `any` / `k-of: N` each advance exactly at their threshold, counted
  across **distinct** identities; a duplicate approval from the same identity does not double-count.
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
