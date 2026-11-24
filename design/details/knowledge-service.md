---
title: Knowledge service — a standalone swarmkit-knowledge package, with SwarmKit as its engine
description: Knowledge bases move out of the runtime into an open, standalone package with its own CLI, HTTP and MCP server, store, workers and portal, usable by any agent. LLM work (enrichment, figure descriptions, answers) runs as SwarmKit topologies, embedded or against a swarmkit serve. SwarmKit is one client, through a workspace block that points at a service or starts a local one. Owner-keyed tables, a BlobStore interface, queued workers, sandboxed conversion, usage events and OAuth connectors keep it hostable later.
tags: [knowledge, packaging, service-layer, mcp, engine, hosting]
status: proposed
---

# Knowledge service: a standalone package, with SwarmKit as its engine

**Status:** proposed (#1140, under #1127). Revises `knowledge-bases.md`, which stays the reference for *what* a knowledge base is (kinds, OKF bundles, sources, large documents, figures, archives, governance, the read contract). This note changes *where it lives* and *how it runs*.

## Goal

The knowledge service turns documents into knowledge bases that **any** agent can use. That includes SwarmKit's own agents, and also Claude, ChatGPT, Cursor, Copilot and custom LLM apps, over MCP, A2A, `llms.txt` and OpenAI-compatible search. It ships as **`swarmkit-knowledge`**, an open (MIT) package that runs on its own and doesn't need SwarmKit to serve a base. When a build needs an LLM, it uses SwarmKit as its engine.

## Non-goals

- **Hosting in this repository.** The open package is complete for one person or one team on their own machine or server. A hosted service, if one is built, lives elsewhere. This note only requires that the open package doesn't block hosting (§6).
- **Enterprise-only features:** SSO/SAML, SCIM, permission-synced connectors, and compliance exports. They're not planned.
- **A server per base.** One service hosts every base. A base is a row, and its endpoints are routes.
- **Changing what a base is.** `knowledge-bases.md` §1–§3 and §5–§12 hold unchanged, except where §3–§5 below move them.

## Why a separate package

1. **Most of a build needs no model.** Fetching, converting, splitting into sections, extracting figures, keyword indexing and local embeddings (fastembed, on CPU) all run without an LLM. A plain `rag` base needs no API keys and no runtime.
2. **Most users of a base aren't SwarmKit agents.** Inside the runtime, its value would reach only SwarmKit users. As its own service, SwarmKit is one client among many.
3. **SwarmKit already reaches it the right way.** `knowledge-bases.md` §3 grants agents a base through an MCP server and `pack:knowledge`. The runtime never needed the build pipeline in-process.
4. **Being hostable later is cheap now and expensive to add later** (§6).

## Design

### 1. Packages and dependencies

```
packages/knowledge/          swarmkit-knowledge (Python): service, store, CLI, HTTP + MCP server, workers
packages/knowledge-ui/       the knowledge portal (Next.js), shipped inside swarmkit-knowledge like webui
packages/schema/             gains knowledge.schema.json (the service's own config), keeps the workspace block
```

- **It depends on `swarmkit-schema`.** The base declaration has one schema, shared by the workspace block and the service's own config.
- **The engine is an extra:** `swarmkit-knowledge[engine]` adds `swarmkit-runtime`. Without it, LLM work is unavailable (§2).
- **The runtime never depends on it.** `swarmkit-runtime[knowledge]` is a convenience extra that installs the package. The runtime talks to it only through its HTTP/MCP contract, as the control plane does. A contract test, not a shared import, keeps the two in step.
- **Other extras:** `[docs]` adds Docling (OCR, layout). `[vector]` adds sqlite-vec and fastembed. `[postgres]` adds the Postgres store.

This one-way dependency differs from the control plane, which must never depend on the runtime because it manages many runtimes of different versions. The knowledge service doesn't manage runtimes; it uses one as a library or as a service.

### 2. SwarmKit as the engine

Work that needs an LLM runs as **SwarmKit topologies**, bundled with the package as a small workspace (`swarmkit_knowledge/engine/workspace/`):

| Work | Topology | When |
|---|---|---|
| Enrichment: summaries, concepts, links, contradictions | `knowledge-builder` | `okf` bases, `enrich.when` |
| Figure descriptions | `knowledge-describe-figure` | `images.describe: true` |
| Cited answers | `knowledge-answer` | the `ask` tool, A2A `kb-<id>-qa` |
| Ingest screening | `knowledge-ingest-screen` (a decision skill) | `screen` |
| Cloud embeddings | through `ModelProvider.embed()` once it exists (`knowledge-bases.md` phase 3) | `defaults.search: hybrid` with a cloud model |

This gives the knowledge service, for free:
- any model provider, local or cloud;
- per-call audit and cost tracking;
- budgets;
- governance on what agents write;
- enrichment that can be changed by editing YAML, not code.

**Two ways to connect:**
- **Embedded** (`engine: {mode: embedded}`, the default with `[engine]` installed): a `WorkspaceRuntime` in the build worker, over the bundled workspace merged with the user's `model_providers`.
- **Remote** (`engine: {mode: remote, url, credential}`): the build worker calls `POST /run` on a `swarmkit serve` and follows the job. This suits teams, and keeps the service and its workers light.

Each engine run is a normal SwarmKit job. It's linked from the build, and its cost is recorded against the build (§6.4).

### 3. The service and its front doors

`KnowledgeService` (`swarmkit_knowledge/_service.py`) keeps the operations in `knowledge-bases.md` §4, moved into this package. Every front door goes through it:
- **CLI:** `swarmkit-knowledge` (alias `skb`). The runtime's `swarmkit knowledge …` forwards to it when installed, so the three-command path in `knowledge-bases.md` stays the same.
- **HTTP API:** `/v1/bases/…`, uploads, builds, reviews.
- **MCP:** one endpoint per base, `/kb/<id>/mcp`, plus `/mcp` for every base the caller can see.
- **The rest:** A2A `kb-<id>-qa`, `/kb/<id>/llms.txt`, and `POST /v1/vector_stores/<id>/search`.
- **The portal:** creation, uploads, Browse, Search, Review, Activity, Connect.

`swarmkit-knowledge serve` runs the API, the portal and a worker in one process, which is enough for one person or team. `swarmkit-knowledge worker` runs extra workers.

### 4. Governance, now owned by the service

The runtime's `GovernanceProvider` never sees an outside agent, so the knowledge service enforces `knowledge-bases.md` §5 itself:
- **Principals:**
  - people, through local accounts or a reverse-proxy identity header;
  - agents, through API tokens scoped to bases and roles.
- **Roles per base:** `reader`, `contributor` (can propose), `reviewer` (can approve or reject), `verifier` (can verify).
- **Verifying is reserved for humans,** structurally: a token can never carry `verifier`, whatever it's issued for. This is the service's equivalent of the runtime's reserved `knowledge:verify` scope.
- **The audit log is append-only.** Every write, review and verification is recorded, and no API updates or deletes an entry.
- **Inside SwarmKit, both layers apply.** An agent first needs a grant (`pack:knowledge` / `knowledge:<id>`). The runtime then calls the service with a token for that workspace, and the service applies its roles. The runtime's audit records the call; the service's audit records the change.

### 5. SwarmKit as a client

The workspace `knowledge` block (#1139) gains `knowledge.service` (a schema minor):

```yaml
knowledge:
  service: { url: https://kb.internal.example, credential: kb-token }   # use a running service
  # or omitted: `swarmkit serve` starts a local one for this workspace (managed mode)
  bases: [...]   # declared here, created on the service if missing
```

- **Managed mode** (no `service`) is the solo path. `swarmkit serve` starts `swarmkit-knowledge serve` as a child process on a local port, with its store under `.swarmkit/knowledge/`. It's registered as the `knowledge` MCP server the grants resolve to. If the package isn't installed, `validate` says `pip install "swarmkit-runtime[knowledge]"`.
- **Connected mode** (`service.url`) is for teams. Bases live on a shared service, and the workspace only names them.
- **Reachability** (#1139): a base counts as wired once the service reports it built. Until then, it stays "not built yet".

### 6. Hosting-ready from the start

These belong in the open package because they're expensive to retrofit, and each one is useful even on a single machine:

1. **Every table is keyed by `owner_id`.** The open package uses one owner, `local`. A host can separate many users on shared infrastructure without a schema migration.
2. **`BlobStore` is an interface.** It holds originals, extracted figures and materialized bundles. The default is a local directory (so a bundle is a plain folder you can put in git). An S3-compatible backend (S3, R2, MinIO) is the second implementation. The database never holds file bytes.
3. **Builds are queued jobs claimed by workers.** They use the `claim_queued` pattern already proven in the runtime's Postgres store (atomic claim, heartbeat, sweep). One process can run everything, or workers can scale out and down to zero.
4. **Usage events are recorded**, per owner and base: pages converted, images extracted, figures OCR'd, figures described, embedding tokens, engine cost, queries served and bytes stored. In the open package they feed `skb usage` and the portal. A host can meter them.
5. **Conversion runs in a separate, restricted process.** Parsers for PDFs, Office files and archives handle untrusted input and are a common attack surface. Each conversion runs in a child process with no access to the store or network, CPU, memory and time limits, and a temporary directory deleted afterwards. It talks to the worker over stdin/stdout. A host can swap the process launcher for a container or microVM (gVisor, Firecracker) behind the same interface (`ConversionSandbox`).
6. **Connectors sign in through OAuth** (Google Drive, Notion, GitHub), with tokens in the service's encrypted credential store and refreshed automatically. Pasted tokens remain an option. This matches the runtime's `CredentialService` contract without importing it.
7. **Bundles export to git, but git isn't the store.** `skb export --git <remote>` pushes a base's bundle, and `skb import` reads one back. The service never runs a git server per base.

### 7. What hosting would take (for sizing; not built here)

| Part | Role | Hardware |
|---|---|---|
| API, MCP and portal | Stateless; every base's endpoints | Small, always-on; 1–2 vCPU each, scaled horizontally |
| Postgres + pgvector | Bases, builds, the index (tsvector + vectors), audit, usage | One managed instance; the main fixed cost |
| Object storage | Originals, figures, bundles | S3 or R2 |
| Build workers | Convert, split, OCR, embed, call the engine | Short-lived containers from the queue; 2–4 vCPU, 4–8 GB RAM (Docling needs 2–4 GB); scale to zero |
| Engine | Enrichment, descriptions, answers | `swarmkit serve`, calling cloud models through `ModelProvider` |
| Scheduler and crawler | Refresh, connector sync, fetching | Small; crawl workers with restricted egress (SSRF rules from `knowledge-bases.md` §12) |

**Rough estimates per 500-page manual** (about 1–3 MB of Markdown and about 1,500 chunks), to be replaced by phase 1 measurements:
- **Converting and splitting:** about 1–3 CPU-minutes.
- **OCR:** the heaviest CPU step, minutes for a scanned manual.
- **Local embeddings:** tens of seconds on CPU.
- **Figure descriptions:** about 0.1–1¢ per image with a cheap vision model.
- **Enrichment:** about 10–50¢ with a cheap model.
- **Storage:** about 1.5 KB per 384-dimension vector before index overhead, so a million chunks is about 1.5 GB. pgvector is comfortable at a few million.
- **A search query:** milliseconds of database time.

**Starting size:** one 4–8 vCPU, 16 GB VM running Postgres, the API and one worker, plus object storage. No GPUs: models go through APIs, and embeddings and OCR run on CPU. If OCR or vision volume justifies it, GPUs rented by the second can serve that one step.

## Changes to `knowledge-bases.md`

- **§4:** `KnowledgeService` moves from `swarmkit_runtime/knowledge/` to `swarmkit_knowledge/`. Builds use the service's own job queue (§6.3), not the runtime's `knowledge_builds` table or job semaphore. Engine runs remain SwarmKit jobs.
- **§5:** enforcement moves to the service's roles and tokens (§4 here). The runtime's reserved `knowledge:verify` scope stays, for SwarmKit grants.
- **§7:** serve's `/knowledge` routes become the knowledge service's API. SwarmKit's portal gets a Knowledge page that links to the knowledge portal and shows each base's status, without its own creation flow.
- **Non-goals:** "a hosted knowledge service" now reads "hosting in this repository".
- **Phase 1** (#1138): 1a (#1139) is unchanged. 1b creates `packages/knowledge` (service, store, queue, BlobStore, conversion sandbox, Markdown and text pass, FTS5, CLI). 1c adds its MCP and HTTP front doors, plus the runtime's `knowledge.service` and managed mode. 1d adds the sources and figures. 1e adds the knowledge portal. The exit demo also queries the base from an MCP client with no SwarmKit installed.

## Test plan

- **Package boundary:** `swarmkit-knowledge` installs and serves a base with `swarmkit-runtime` absent, and its test suite runs in a job without the runtime. The runtime's tests never import `swarmkit_knowledge`. A contract test pins the HTTP and MCP shapes both sides use.
- **Owner keys:** two owners' bases, builds, figures and usage never appear in each other's lists, searches or fetches.
- **BlobStore:** the local and S3-compatible (MinIO in CI) backends pass one shared suite.
- **Queue:** two workers never claim one build; a killed worker's build is swept back to `queued` and resumes.
- **Sandbox:** a converter that tries to open the store, reach the network or exceed its memory or time limit fails with a named reason, and the worker survives.
- **Roles:** a token can't verify; a `contributor` proposes but can't approve; every write leaves an audit entry; no API mutates the audit log.
- **Engine:** an enrichment run in embedded mode and one against a `swarmkit serve` produce the same proposals on the mock provider, and both record cost against the build.
- **Managed mode:** `swarmkit serve` on a workspace with a `knowledge` block starts the service, grants resolve to it, and stopping serve stops it.

## Demo plan

`just demo-knowledge-service`:
1. `pip install swarmkit-knowledge` with no runtime.
2. `skb create handbook ./docs` and `skb serve`.
3. The base is queried from an MCP client over `/kb/handbook/mcp`.
4. With `[engine]` installed, `skb enrich handbook` shows the engine's job and its cost.
5. The same base is used from a SwarmKit workspace in connected mode.

## Open questions

1. **License.** MIT, the same as SwarmKit (proposed), or AGPL to stop someone else reselling it as a hosted service. MIT keeps adoption simple and matches the rest of the repository.
2. **Names:** the CLI (`swarmkit-knowledge` with alias `skb`), and whether the package name should drop the `swarmkit-` prefix to read as usable without SwarmKit.
3. **Local accounts versus identity header only** for people in the open package's portal.
