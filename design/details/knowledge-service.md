---
title: Knowledge service — SwarmKit's side of the standalone swarmkit-knowledge package
description: Knowledge bases are served by swarmkit-knowledge, a standalone package in its own repository that any agent can use. SwarmKit keeps the base declaration schema, acts as one client (a workspace block that points at a service or starts a local one; grants resolve to its MCP endpoint), and is the engine for the service's model work. The dependency is one-way and the two meet only at an HTTP/MCP contract.
tags: [knowledge, packaging, service-layer, mcp, engine]
status: proposed
---

# Knowledge service: SwarmKit's side

**Status:** proposed (#1140, under #1127). Revises `knowledge-bases.md`, which stays the reference for what a knowledge base is: kinds, OKF bundles, sources, large documents, figures, archives, governance and the read contract.

## The decision

The knowledge service doesn't live in the runtime. It's **`swarmkit-knowledge`**, a standalone, open (MIT) package **in its own repository**, with its own CLI (`swarmkit-knowledge` / `skb`), HTTP and MCP server, store, workers, governance and portal. Any agent can use it: Claude, ChatGPT, Cursor, Copilot, custom LLM apps and SwarmKit. That repository holds the service's own design: packaging, the engine connection, its governance, and a shape that keeps it hostable later.

**Why it's separate:**
- **Most of a build needs no model.** Converting, splitting into sections, figures, keyword indexing and local embeddings all run without one.
- **Most users of a base aren't SwarmKit agents.**
- **SwarmKit already reaches a base the right way,** through an MCP server and grants (`knowledge-bases.md` §3).

## What stays in this repository

### 1. The declaration schema

The `knowledge` block in `workspace.schema.json` (#1139) is the one schema for a knowledge-base declaration. `swarmkit-knowledge` depends on `swarmkit-schema` from PyPI and validates its own config against the same `knowledge_base` definition. A schema change follows `docs/notes/schema-change-discipline.md` here, and then needs a release before the service can use it.

### 2. SwarmKit as a client

The block gains `knowledge.service` (a schema minor):

```yaml
knowledge:
  service: { url: https://kb.internal.example, credential: kb-token }   # use a running service
  # or omitted: `swarmkit serve` starts a local one for this workspace (managed mode)
  bases: [...]   # declared here, created on the service if missing
```

- **Managed mode** (no `service`) is the solo path:
  - `swarmkit serve` starts `swarmkit-knowledge serve` as a child process on a local port, with its store under `.swarmkit/knowledge/`, and stops it on shutdown.
  - The service is registered as the `knowledge` MCP server that `pack:knowledge` / `knowledge:<id>` grants resolve to.
  - `swarmkit knowledge …` forwards to `skb`, so the three-command path in `knowledge-bases.md` stays the same.
  - Without the package, `validate` says `pip install "swarmkit-runtime[knowledge]"`.
- **Connected mode** (`service.url`) is for teams. Bases live on a shared service, and the workspace names them. The credential is a service token issued to this workspace.
- **Governance is layered.** An agent needs a grant first, which the runtime's policy engine enforces and audits. The service then applies its own role for the workspace's token, and records the change in its own append-only audit. The human-reserved `knowledge:verify` scope stays a runtime reserved scope, and the service separately refuses verification from any token.
- **Reachability** (#1139): a base counts as wired once the service reports it built. Until then, `validate` keeps reporting it as "not built yet".

### 3. SwarmKit as the engine

The service's model work runs as SwarmKit topologies:
- `knowledge-builder` (enrichment);
- `knowledge-describe-figure`;
- `knowledge-answer`;
- the `knowledge-ingest-screen` decision skill.

They ship with the service's package as a small bundled workspace. The service runs them in one of two ways:
- **embedded:** `swarmkit-knowledge[engine]` creates a `WorkspaceRuntime` in its build worker;
- **remote:** it calls `POST /run` on a `swarmkit serve` and follows the job.

Either way, each run is an ordinary SwarmKit job, with its cost recorded, through `JobService`. Nothing new is needed in the runtime for this beyond `ModelProvider.embed()`, which `knowledge-bases.md` already plans for phase 3.

### 4. The contract

The two sides share no code.
- **The dependency is one-way:** the service may import `swarmkit-runtime` (the `[engine]` extra), and the runtime never imports `swarmkit_knowledge`. A boundary test here enforces that.
- **The HTTP and MCP shapes the runtime calls are pinned** as a contract fixture in `packages/runtime/tests/fixtures/knowledge-contract/`. The runtime's client tests run against it.
- **The service's CI runs the same shapes** against the released runtime.
- **Changing the contract** takes a PR in each repository.

## Changes to `knowledge-bases.md`

- **§4, §5 and §7:** `KnowledgeService`, builds, the service's governance, the portal and the HTTP routes move to `swarmkit-knowledge`. Here, serve keeps a Knowledge page that lists the workspace's bases with their status and links to the knowledge portal.
- **Non-goals:** "a hosted knowledge service" now reads "hosting in this repository".
- **Phase 1** (#1138): 1a (#1139) is done here. 1b–1e (the service, its front doors, sources and figures, and its portal) move to the `swarmkit-knowledge` repository. This repository then adds `knowledge.service`, managed mode, grants resolving to the service, and the contract tests.

## Test plan (this repository)

- **The boundary:** no runtime module imports `swarmkit_knowledge`, and a test enforces it.
- **Managed mode:**
  - `swarmkit serve` on a workspace with a `knowledge` block starts the service, `pack:knowledge` resolves to it, and stopping serve stops it;
  - when the package is missing, `validate` names the extra to install.
- **Connected mode:** grants resolve to the configured URL with the workspace's token, and a refused call is audited as a denial.
- **The contract:** the runtime's client passes against the pinned fixture.
- **Reachability:** a base is wired once the service reports it built, and "not built yet" until then.

## Demo plan

`just demo-knowledge-client`:
1. A workspace with a `knowledge` block and `swarmkit serve` in managed mode.
2. An agent with `pack:knowledge` answers from the base, with citations.
3. The same base is queried from an MCP client directly.
