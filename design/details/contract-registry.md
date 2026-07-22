---
title: Integration-contract registry
description: Make the integration contracts a pipeline locks on first-class artifacts, so lock ids are a checked, pickable vocabulary (not free strings) and the contention overlay is exact.
tags: [schema, pipeline, contracts, locking]
status: proposed
---

# Integration-contract registry

**Scope:** schema (new `Contract` artifact) + runtime (discovery/resolution + StageGraph lock
ref-check) + serve + composer + docs.
**Design references:** [`pipeline-controller.md`](pipeline-controller.md) (integration-contract
locking), [`pipeline-editor-canvas.md`](pipeline-editor-canvas.md) (this is its "contract-lock
registry" open question, resolved), [`stage-graph` schema](../../packages/schema/schemas/stage-graph.schema.json).
**Status:** proposed.

## Why

A pipeline serialises requirements on the **integration contracts** they share — a stage's
`locks: [contract:oms-web, contract:oms-inventory]` mean "hold the OMS↔Web and OMS↔Inventory
interfaces while I change them, so no concurrent requirement commits a conflicting version." Today
those lock ids are **free-form strings**: nothing checks them, a typo (`contract:oms-web`) silently
becomes a *different* lock (so two requirements that should serialise don't), the editor can only
offer a free-text chip, and the contention overlay ("which stages fight over the same contract") is
approximate. The contract itself — the agreed interface between two apps — has no home.

Make each integration contract a **first-class artifact**. Then lock ids are a **checked, pickable
vocabulary**: the resolver rejects a lock that names no contract, the editor offers a picker over
real contracts, and contention is exact.

## The `Contract` artifact

A contract is the agreed interface between two (or more) applications, identified by id.

```yaml
apiVersion: swarmkit/v1
kind: Contract
metadata:
  id: oms-web
  name: OMS ↔ Web order API
  description: The order-submission + status API OMS exposes to the Web storefront.
parties: [oms, web]            # the apps this contract binds (>= 2)
interface: schemas/oms-web-order.json   # optional: where the interface itself lives
provenance:
  authored_by: human
  version: 1.0.0
```

- **`parties`** (required, ≥2) — the apps the contract is between. This is what makes it a contract
  (an interface between apps), and it drives the editor's contention/ownership display. App ids are
  free strings (apps are not artifacts).
- **`interface`** (optional) — a pointer to the actual interface spec (an API/event schema). Not
  interpreted by core; documentation + a handle for reviewers.

## What changes in the StageGraph

A stage's `locks` items become **contract references**:

```json
"locks": {
  "type": "array",
  "items": { "$ref": "#/$defs/identifier", "x-swarmkit-ref": "contract" }
}
```

- The resolver ref-checks each lock against the contract registry — an unknown contract is a
  `stage-graph.unknown-contract` resolution error (consistent with how `topology`/`gate` refs are
  checked). In this model a lock **is** an integration contract; the free-string form was the
  placeholder. `release_locks_on` is unchanged (it's an event, not a contract).
- **Migration:** the contract ids drop the redundant `contract:` prefix — the field is `locks`, so
  the id is just `oms-web`. The OMS example gains `contracts/oms-web.yaml` + `oms-inventory.yaml` and
  its design stage becomes `locks: [oms-web, oms-inventory]`.

## Runtime

- `contract` joins the discoverable artifact kinds (`contracts/` directory), resolved into a
  `ResolvedWorkspace.contracts` registry (id → `ResolvedContract`), like funnels/roles/stage-graphs.
- StageGraph resolution gains the lock ref-check against that registry.
- The contract itself is not *executed* — the controller/orchestrator is still the lock manager; the
  registry only makes the vocabulary real. A contract's `parties` let the manager (and the board)
  group locks by app-pair.

## Composer + serve

- Serve CRUD: `/contracts`, `/api/contracts/{id}` (mirrors funnels/pipelines).
- `use-ref-options` fetches contracts; the StageGraph editor's `locks` field renders as a
  **RefChips picker over workspace contracts** (the existing `x-swarmkit-ref` array machinery — no
  new UI mechanism), replacing the free-entry chips.
- A **Contracts** artifact surface (list + schema form), like funnels.
- **Exact contention overlay:** the pipeline canvas highlights stages that hold the *same* contract
  id (now guaranteed to be the same real contract), and can label a contract by its `parties`.

## Docs / authoring

Contract joins the artifact-kind enumerations: `llms.txt` (a Contract section), a reference page, the
authorable-kinds list, and the knowledge-server write-path (`contracts/`). The knowledge server
auto-globs schemas, so `get_schema("contract")` surfaces for free.

## Non-goals

- **Not contract *content* validation.** Core does not parse or diff the `interface` spec — that is
  the SIT/contract-testing stage's job (a later slice). The registry governs *identity + locking*,
  not interface compatibility.
- **Not app artifacts.** `parties` are free strings; apps do not become a kind here.
- **Not a new lock manager.** Locking stays in the orchestrator (reference controller / Temporal
  adapter); this only makes lock ids checkable and pickable.

## Test plan

- **Schema (Py + TS):** a valid contract parses; missing `parties`/`<2 parties` is rejected; the new
  `contract` schema round-trips its fixtures.
- **Resolution:** contracts discovered into `ResolvedWorkspace.contracts`; a StageGraph lock naming a
  real contract resolves; an unknown lock is `stage-graph.unknown-contract`; the OMS workspace
  resolves with its two contracts.
- **UI:** `locks` renders as a contract RefChips picker; the contention overlay groups stages by
  shared real contract.

## Demo plan

Extend `just demo-pipeline-controller` / the pipelines UI: two OMS requirements both locking
`oms-web` serialise (already demoed) — now the lock is a *checked* contract, a typo'd lock fails
resolution loudly, and the editor's `locks` field is a dropdown of the workspace's contracts.
