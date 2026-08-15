# Contract

A **contract** is a first-class SwarmKit artifact (`kind: Contract`) that names an **integration contract**: the agreed interface between two (or more) applications, identified by id. It exists so the locks your sequencer holds reference **real** contracts instead of free-form strings — a checked, pickable vocabulary rather than opaque lock names.

Why the registry exists, the lock ref-check, and the non-goals are specified in the design note: [integration-contract registry](https://github.com/delivstat/swarmkit/blob/main/design/details/contract-registry.md) (`design/details/contract-registry.md`). This page is the artifact reference.

## What a contract is for

A delivery flow serialises work on the **integration contracts** it shares. `locks: [oms-web, oms-inventory]` mean "hold the OMS↔Web and OMS↔Inventory interfaces while I change them, so no concurrent requirement commits a conflicting version." Those locks are what keep two pieces of work that both touch the same app-pair interface from racing.

Before contracts were artifacts, lock ids were **free-form strings**: nothing checked them, so a typo (`oms-web` vs `oms_web`) silently became a *different* lock — and two requirements that should serialise did not. The composer could only offer a free-text chip, and the contention overlay ("which stages fight over the same contract") was approximate. The contract itself — the agreed interface between two apps — had no home.

Making each integration contract a first-class artifact turns lock ids into a **checked, pickable vocabulary**: the resolver rejects a lock that names no contract, the editor offers a picker over real contracts, and contention is exact.

## Locking and contention

A lock **is** an integration contract; the free-string form was the placeholder. A stage acquires its `locks` **all-or-none, in a fixed global order**, *before* its run starts (deadlock avoidance), and releases them on the signal named by `release_locks_on` — for example, hold a contract through design approval, then release on `design.approved`. Unrelated requirements whose stages lock **disjoint** contracts still run in parallel; only stages that hold the **same** contract id serialise.

A contract is **not executed**. **Your application is the lock manager** — SwarmKit stopped sequencing anything in 1.189.0 (see [Extracting the pipeline](../design-notes/extracting-the-pipeline.md)); the registry only makes the vocabulary real. A contract's `parties` let that manager group locks by the app-pair they bind, which is what makes a contention view exact and labelable.

## Referenced by locks

A contract is a standalone artifact, like a skill or a funnel. It lives in a `contracts/` directory in the workspace and is referenced **by id** by whatever holds the lock. Defining it once and referencing it by id is what lets many pieces of work hold the same real interface, and lets the composer `ref`-validate lock names against the workspace before publish.

Contracts are discovered into `ResolvedWorkspace.contracts` (id → resolved contract, like funnels and roles), so a sequencer can resolve a lock name to a real artifact — and reject one that names nothing — instead of trusting a string. The ref-check that ran against a `StageGraph`'s `locks` went with the stage graph itself; the registry it checked against did not.

## Contract fields

| Field | Required | What it does |
|---|---|---|
| `parties` | yes | The applications this contract binds — **at least two**. This is what makes it a contract (an interface *between* apps), and it drives the contention / ownership display. App ids are free strings; apps are **not** artifacts. |
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

Your sequencer then holds them by id. The shape is **yours** — SwarmKit no longer defines one — but
the ids are checkable against the workspace, which is the whole point of the registry:

```python
# your orchestrator, your dataclass
Stage(id="design", topology="sdlc-design", locks=("oms-web", "oms-inventory"))
```

`ResolvedWorkspace.contracts` is how you check a lock name before you take it, so a typo fails where
you can see it rather than silently becoming a different lock that serialises nothing.

## Authoring a contract

The conversational authoring path treats a contract like any other artifact: the schema drafter calls `get_schema("contract")` for the exact shape, and `query-swarmkit-docs` surfaces this reference and the design note. The authoring swarm writes the artifact into the workspace `contracts/` directory via `write_workspace_file`. When authoring, remember: `parties` needs **at least two** app ids (a contract is an interface *between* apps); the `id` is what a lock references, so it must match the lock your sequencer expects; and `interface` is optional and documentation-only — core never parses it.

## See also

- [Integration-contract registry design note](https://github.com/delivstat/swarmkit/blob/main/design/details/contract-registry.md) — why lock ids become a checked vocabulary and the non-goals (no interface-content validation, no app artifacts, no new lock manager).
- [Driving SwarmKit from your application](orchestrator-integration.md) — where the lock manager lives now.
