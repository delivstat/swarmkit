# Level 18: Funnels and multi-party approval

Gate an agent's output with a **funnel** — validate, judge, then a human approval that a prompt cannot
talk past — and the reports that tell you before a run whether the gate is real.

## What you'll learn

- The three layers of a funnel: `validate` (deterministic), `judge` (a decision skill), `approve` (people)
- Role registries and approval policies: quorum, distinct approvers, author exclusion
- Defer-and-resume: a parked run stays parked, not resident
- Resolving a gate as an identity — CLI, HTTP, the portal — and reading its state with the policy applied
- Two pre-run reports: `validate --require` and `--require-verified`
- Two diff checks for review slices: `cited-change` and `slice-check`
- Stopping a run cooperatively

## The idea

A decision skill can say `fail` and the agent revises. That is quality, not authority. **Authority** is
a human gate, and SwarmKit makes it structural: the scopes that resolve a gate (`approvals:resolve`
and friends) cannot be granted to an agent by any prompt or config ([design §8.7](../architecture/design-overview.md)).
A funnel packages the ordered layers so the same gate can be reused across topologies
([Gate funnel](../design-notes/gate-funnel.md)).

## Build it

### 1. The people

```yaml
# roles/roles.yaml
apiVersion: swarmkit/v1
kind: RoleRegistry
metadata: { id: roles, name: Roles }
roles:
  - id: release-manager
    scopes: [release:approve]
    members: [alice, bob]
```

### 2. The funnel

```yaml
# funnels/release-approval.yaml
apiVersion: swarmkit/v1
kind: Funnel
metadata: { id: release-approval, name: Release approval }
validate:
  schema: schemas/release-note.json        # shape, free, deterministic
  slice_budget: { max_files: 20, max_diff_lines: 400 }
  cited_change: true                       # the rationale must cite what the diff touched
judge:
  skill: release-risk-verdict              # a decision skill; score below threshold retries
  threshold: 0.8
  max_retries: 2                           # then it escalates to the humans with the critique attached
approve:
  rules:
    - scope: release:approve
      roles: [release-manager]
      quorum: all                          # all | any | { k-of: 2 }
  exclude_author: true
  min_distinct_approvers: 1
```

Attach it to the node whose output it gates:

```yaml
# topologies/release.yaml (excerpt)
agents:
  root:
    id: coordinator
    role: root
    funnel: release-approval
```

### 3. Check it before you run

```bash
swarmkit validate ./workspace --require            # config no code path reaches
swarmkit validate ./workspace --require-verified   # topology roots whose output nothing checks
```

```
reachability: 0 declared, all wired

verification: 1 topology root(s)
  hello/root (root): no funnel — its output is checked by nothing
```

The second line is the one to read: a root with no funnel produces "whatever the model said".

Over HTTP, `GET /funnels` and `GET /contracts` list what the workspace declares, by id — the same
registries the portal's Funnels and Contracts pages read.

## Run it

`just demo-showcase` is this tutorial end to end over HTTP, on the mock provider: a run parks on the
gate, `GET /events` announces `funnel.gate_opened`, `GET /gates/{id}` names the outstanding role,
`POST /review/{id}/resolve` records who decided, and the run resumes on its own.

By hand, with the CLI:

```bash
SWARMKIT_PROVIDER=mock swarmkit run ./workspace release --input "Release 1.2.0"
# ... deferred: gate 'run-…:coordinator' awaits approval

swarmkit review list ./workspace --kind role_task
swarmkit review gate <gate-id> ./workspace                       # resolved? policy applied
swarmkit review resolve <item-id> --as alice --approve ./workspace
swarmkit run ./workspace release --resume <run-id>               # or let serve resume it
```

`--as alice` is checked against the role registry; an identity not in the role, or the artifact's own
author under `exclude_author`, is refused with the reason.

Two checks you can run on any diff, the same ones the `validate` layer runs:

```bash
swarmkit cited-change --rationale rationale.yaml --diff change.diff   # exit 1 if a citation names code the diff did not touch
swarmkit slice-check --diff change.diff --max-files 20 --max-diff-lines 400
```

And stopping, which is a deferral with a different reason:

```bash
swarmkit stop <run-id> ./workspace          # lands at the next agent boundary; a call in flight finishes
swarmkit run ./workspace release --resume <run-id>
```

## What happened

- `validate` ran first and cost nothing; `judge` cost one model call per round; `approve` cost a
  person — and the run was not resident while it waited (checkpointed, job `deferred`).
- The gate id is `<run>:<agent>`, unique to this run: an approval cast on a previous artifact cannot
  satisfy a new one.
- `GET /gates/{id}` (and `review gate`) answers "is it resolved" **with the policy applied** — quorum
  and distinct-approver counting are not something every client should reimplement.

## Learn more

- [Funnel artifact](../reference/funnel.md) · [Role registry](../reference/role-registry.md) · [Approval policy](../reference/approval-policy.md)
- [Reading a gate, approving without a saga](../design-notes/gate-state-and-deferring-approval.md)
- [Multi-party approval](../design-notes/multi-party-approval.md) · [Funnel verification strength](../design-notes/funnel-verification-check.md)
- [Stopping a run](../design-notes/stopping-a-run.md)
- [Validating a topology's output](../guides/validating-topology-output.md) — decision skills, the layer under the gate
