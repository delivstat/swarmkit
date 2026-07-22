# Contract

A **contract** is a first-class SwarmKit artifact (`kind: Contract`) that names an **integration contract**: the agreed interface between two (or more) applications, identified by id. It exists so a [StageGraph](stage-graph.md) stage's `locks` reference **real** contracts instead of free-form strings — a checked, pickable vocabulary rather than opaque lock names.

Why the registry exists, the StageGraph lock ref-check, and the non-goals are specified in the design note: [integration-contract registry](https://github.com/delivstat/swarmkit/blob/main/design/details/contract-registry.md) (`design/details/contract-registry.md`). This page is the artifact reference.

## What a contract is for

A pipeline serialises requirements on the **integration contracts** they share. A stage's `locks: [oms-web, oms-inventory]` mean "hold the OMS↔Web and OMS↔Inventory interfaces while I change them, so no concurrent requirement commits a conflicting version." Those locks are what keep two requirements that both touch the same app-pair interface from racing.

Before contracts were artifacts, lock ids were **free-form strings**: nothing checked them, so a typo (`oms-web` vs `oms_web`) silently became a *different* lock — and two requirements that should serialise did not. The composer could only offer a free-text chip, and the contention overlay ("which stages fight over the same contract") was approximate. The contract itself — the agreed interface between two apps — had no home.

Making each integration contract a first-class artifact turns lock ids into a **checked, pickable vocabulary**: the resolver rejects a lock that names no contract, the editor offers a picker over real contracts, and contention is exact.

## Locking and contention

A lock **is** an integration contract; the free-string form was the placeholder. A stage acquires its `locks` **all-or-none, in a fixed global order**, *before* its run starts (deadlock avoidance), and releases them on the signal named by `release_locks_on` — for example, hold a contract through design approval, then release on `design.approved`. Unrelated requirements whose stages lock **disjoint** contracts still run in parallel; only stages that hold the **same** contract id serialise.

A contract is **not executed**. The controller / orchestrator (the reference controller, or a Temporal adapter) is still the lock manager; the registry only makes the vocabulary real. A contract's `parties` let the manager — and the pipeline board — group locks by the app-pair they bind, which is what makes the contention overlay exact and labelable.

## Referenced by locks

A contract is a standalone artifact, like a skill or a funnel. It lives in a `contracts/` directory in the workspace and is referenced **by id** from a StageGraph stage's `locks`. Defining it once and referencing it by id is what lets many stages (across many pipelines) hold the same real interface, and lets the composer `ref`-validate the locks against the workspace before publish.

The runtime **ref-checks each lock against the contract registry**: contracts are discovered into `ResolvedWorkspace.contracts` (id → resolved contract, like funnels/roles/stage-graphs), and a StageGraph lock naming a contract that does not exist is a `stage-graph.unknown-contract` resolution error — consistent with how `topology` and `gate` references are checked. (`release_locks_on` is unchanged; it is an event, not a contract.)

## Contract fields

| Field | Required | What it does |
|---|---|---|
| `parties` | yes | The applications this contract binds — **at least two**. This is what makes it a contract (an interface *between* apps), and it drives the pipeline's contention / ownership display. App ids are free strings; apps are **not** artifacts. |
| `interface` | no | A pointer to where the interface itself lives (an API / event schema). **Not interpreted by core** — documentation plus a handle for reviewers. |

Core does not parse or diff the `interface` spec — that is a contract-testing / SIT concern, not this registry's. The registry governs **identity + locking**, not interface compatibility.

## Schema shape

```yaml
apiVersion: swarmkit/v1
kind: Contract
metadata:
  id: <lowercase-kebab>          # the contract id — this is what `locks` reference
  name: <human name>
  description: <what interface this contract governs>   # min 10 chars
parties: [<app id>, <app id>, ...]   # at least two; free strings, not artifact ids
interface: <path>                    # optional pointer to the interface spec; not parsed by core
provenance:
  authored_by: human
  version: 1.0.0
```

Only `apiVersion`, `kind`, `metadata`, `parties`, and `provenance` are required; `interface` is optional.

## Minimal example

The OMS↔Web order interface, with no pointer to the spec:

```yaml
apiVersion: swarmkit/v1
kind: Contract
metadata:
  id: oms-web
  name: OMS ↔ Web order API
  description: The order-submission + status API OMS exposes to the Web storefront.
parties: [oms, web]
provenance:
  authored_by: human
  version: 1.0.0
```

## Full example

The OMS↔Inventory contract, pointing at the interface spec a reviewer can open:

```yaml
apiVersion: swarmkit/v1
kind: Contract
metadata:
  id: oms-inventory
  name: OMS ↔ Inventory reservation API
  description: The stock-reservation + release events OMS exchanges with Inventory.
parties: [oms, inventory]
interface: schemas/oms-inventory.json    # workspace-relative; documentation only, not parsed by core
provenance:
  authored_by: human
  version: 1.0.0
```

A [StageGraph](stage-graph.md) then locks on these by id:

```yaml
  - id: design
    topology: sdlc-design
    when: [design.kickoff]
    locks: [oms-web, oms-inventory]      # Contract references — ref-checked at resolution
    release_locks_on: design.approved
```

## Authoring a contract

The conversational authoring path treats a contract like any other artifact: the schema drafter calls `get_schema("contract")` for the exact shape, and `query-swarmkit-docs` surfaces this reference and the design note. The authoring swarm writes the artifact into the workspace `contracts/` directory via `write_workspace_file`. When authoring, remember: `parties` needs **at least two** app ids (a contract is an interface *between* apps); the `id` is what a stage's `locks` reference, so it must match the lock the pipeline expects; and `interface` is optional and documentation-only — core never parses it.

## See also

- [Integration-contract registry design note](https://github.com/delivstat/swarmkit/blob/main/design/details/contract-registry.md) — why lock ids become a checked vocabulary, the StageGraph lock ref-check, and the non-goals (no interface-content validation, no app artifacts, no new lock manager).
- [StageGraph](stage-graph.md) — the pipeline whose stage `locks` reference contracts by id.
