---
title: Knowledge bases — create, govern and expose them easily, with OKF as the format
description: One KnowledgeService behind every front door. A knowledge base is an OKF v0.3 bundle in the workspace repository (the source of truth) plus a rebuildable search index. Agents read through one grant on a runtime-shipped `knowledge` MCP server and write only by proposal; only a person can verify. External stores (GBrain, Graphiti, Basic Memory, Qdrant) come in as data-file adapters. Every base is reachable by any agent over MCP, A2A, llms.txt and an OpenAI-compatible search route.
tags: [knowledge, okf, rag, governance, service-layer, serve, portal, mcp, a2a]
status: proposed
---

# Knowledge bases

**Scope:** `packages/schema` (a `knowledge` block in the workspace schema), `packages/runtime` (a `knowledge/` package, persistence, CLI, serve, MCP, A2A, governance), `packages/ui` (a Knowledge page), the `swarmkit-skills` catalogue (adapter bundles).
**Issue:** #1127 (the umbrella, with the full feature request and the research evidence). Phase 0 also covers #1133 (`mcp-serve` `search_knowledge`) and #1134 (renaming `knowledge-server` and `knowledge-pack`).
**Supersedes:** `user-knowledge-server.md` (generated Python per workspace, which conflicts with "artifacts are data") and the vector-first parts of `knowledge-curator.md`. `knowledge-curator-topology.md`'s Karpathy-style wiki is the precedent this note follows; OKF standardises its shape.
**Design reference:** invariants 2 (skills), 4 (`ModelProvider`), 6 (human-only scopes), 8 (the portal ships with the runtime) and 9 (every interface through the same services); [[governed-memory]] (quarantine and review); [[eval-capture]] (data-not-instructions judging); [[serve-hosted-webui]].
**Revised by:** `knowledge-service.md` (#1140). The service moves out of the runtime into `swarmkit-knowledge`, a standalone package in its own repository, with SwarmKit as its client and engine. §4, §5, §7 and the phase plan are amended there. Where the two notes disagree, `knowledge-service.md` wins.
**Research:** a survey of OKF and LLM wikis, 12 turnkey knowledge-base builders, graph RAG and agent memory, ingestion and local storage, exposure standards, and SwarmKit's current state. The evidence is summarised in #1127.

## Goal

A person or an agent creates a knowledge base in a few steps, from the CLI, the serve portal or chat. Any agent uses it: SwarmKit's own through one grant, others through MCP, A2A, `llms.txt` or an OpenAI-compatible search route. Every fact records who wrote it, whether a person checked it, and whether it's still current, and the runtime enforces that, not the prompt.

## Non-goals

- **Hosting in this repository.** Bases stay files plus a rebuildable index, served by the open `swarmkit-knowledge` package, in its own repository (`knowledge-service.md`). A hosted service, if any, lives elsewhere.
- **A native graph-RAG engine.** Independently measured, graph RAG gains about 3 points on multi-hop questions, costs 40–57x more to build, and answers 19% of unanswerable questions correctly against 96% for plain RAG. Graphs come in as adapters, or as an enrichment skill whose output is reviewable proposals.
- **Replacing governed memory.** Memory holds what agents learn during runs; a base holds what people curate. A one-way bridge (reinforced memory facts proposed into a base) is phase 5.
- **Per-concept access control in v1.** Access is per base. Per-folder ACLs come later.

## What exists, and what this reuses

| Piece | State | Used for |
|---|---|---|
| Governed memory's quarantine, review and resolve flow | shipped | the proposal queue (§5) |
| `pack:` bulk grants and synthesized skills (`pack:workspace`, `pack:<server>`) | shipped | `pack:knowledge`, `knowledge:<id>` (§3) |
| The governed MCP gateway | shipped | harness executors reach the `knowledge` server (§3) |
| `StorageService` (SQLite or Postgres, store kinds) | shipped | `StoreKind.KNOWLEDGE` (§2) |
| The `EvalService` durable-run pattern (row per run, heartbeat, sweep, stop, resume) | shipped | knowledge builds (§4) |
| `TriggerScheduler` in serve | shipped | scheduled refresh (§4) |
| `CredentialService`, the Connections page | shipped | private git repositories, connector sources (§7) |
| Eval capture's redaction and data-not-instructions judging | shipped | ingest redaction; the answer format (§6, §8) |
| `swarmkit skill import` (Agent Skills to `llm_prompt`) | shipped | OKF's `okf-generate` and `okf-consume` (§6) |
| `swarmkit knowledge-server`, `knowledge-pack` | shipped, but for SwarmKit's own docs | renamed to `docs-server`, `docs-pack` (#1134) |
| `mcp-serve`'s `search_knowledge` | broken: swapped arguments, skips governance | fixed now (#1133), then pointed at `KnowledgeService` |
| MarkItDown inside the runtime | **does not exist** (attachments are image-only) | added as a library under the `[knowledge]` extra |
| FTS5, sqlite-vec, pgvector anywhere in the runtime | **none** (only in example servers) | the index (§2) |

**Checked for this note:**
- FTS5 and SQLite extension loading both work in the runtime image (`python:3.11-slim`, SQLite 3.46.1).
- `sqlite-vec` 0.1.9 installs from a wheel and loads.
- macOS's system Python can't load extensions. There, the index falls back to keyword-only, and `knowledge doctor` says so.

## Design

### 1. Kinds, and one contract

| `kind` | What it is | Built by |
|---|---|---|
| `rag` | **Search my documents.** Every source fully converted and indexed: one concept per small source, a tree of section concepts per large one (§2), plus the index. No LLM to build. | the deterministic pass |
| `okf` | **Curated knowledge.** A `rag` base plus synthesized concepts (`Playbook`, `Metric`, `Reference`, …) that cite Source concepts in footnotes. | the deterministic pass + the `knowledge-builder` topology |
| `import` | **An existing OKF bundle**, from a directory, git URL or archive. Read v0.1–v0.3 leniently. | indexing only |
| `gbrain`, `graphiti`, `basic-memory`, `qdrant`, `memory`, `mcp` | **An external store,** through an adapter (§9). | the store itself |

Every kind answers the same read contract (§8). `rag` and `okf` are the same bundle format; `okf` turns enrichment on. A `rag` base can be switched to `okf` later without a rebuild.

### 2. Storage: the bundle is the truth, the index is a cache

**The bundle** is an OKF v0.3 directory at `knowledge/<id>/` in the workspace, tracked in git.
- **Concepts:**
  - The deterministic pass writes `type: Source` concepts. Each has `sources[].resource` pointing at the original, a `description` taken from the first paragraph, `generated: {by: swarmkit-knowledge/<runtime version>, at}`, and `status: draft`.
  - Synthesized concepts cite Sources in footnotes keyed to `sources[].id`, as the spec requires.
- **Reserved files:**
  - `index.md` is written by the service at every level, with `okf_version: "0.3"`, `title`, `description` and `license` at the root.
  - `log.md` is appended by the service only.
- **Extension keys:** SwarmKit-only fields use the spec's `x-swarmkit-*` keys.
  - `x-swarmkit-relations: [{to, type}]` holds typed edges, which OKF defers to a later version.
  - `x-swarmkit-source: {kind, hash, fetched_at}` holds the ingest ledger.
- **`AGENTS.md`** at the bundle root carries the base's schema and conventions for agents. It isn't a concept by OKF's rules.

**Size never limits what's indexed.** Every source is converted and indexed in full, whatever its size; the only limits are the upload limits (§7). Size decides how a source is laid out as concepts.

**Small sources** (converted text up to `section_kb`, 60 KB by default, about 15 pages) become one `type: Source` concept, with the full converted text as its body.

**Large sources, such as a product manual PDF, become a tree of concepts:**
- **Why a directory:** OKF v0.3 defines parent and child only by the directory tree, so a large document gets its own directory, `sources/<document-slug>/`.
- **`overview.md`:** the document's own concept (`type: Source`). It holds the title, the original's `sources[].resource`, page count and the heading outline.
- **One concept per section** (`type: Source Section`), named in reading order (`010-installation.md`, `020-configuration.md`, …), each with its section's full converted text. The service-written `index.md` lists them in order.
- **How it splits:** at the shallowest heading level whose sections fit `section_kb`, going one level deeper for any section that doesn't fit. A document with no usable headings (common in PDFs) is split into page windows, 10 pages by default.
- **Page numbers are kept,** so a citation points at the page: `sources[].resource: upload://<sha256>/manual.pdf#page=42` (the standard PDF fragment), and `x-swarmkit-pages: [42, 51]`.

**Why sections and not one big concept:**
- `fetch` returns a whole concept, and a 2 MB concept would flood an agent's context.
- A 60 KB section fits in about 15k tokens.
- A section is also the right unit to cite, review and verify. A person verifies "Installation", not a 500-page manual.

**The bundle holds the full converted text by default (`materialize: full`).** Converted text is a small fraction of a PDF's size: a 500-page manual is typically 1–3 MB of Markdown. So even large documentation sets suit git.
- **For very large corpora,** where repository size matters (thousands of documents), `materialize: outline` keeps only each document's `overview.md` and section titles in the bundle. The section text then lives in the index and the stored originals.
- **Search and `fetch` are identical in both modes.** `fetch` reads the section text from the index when the bundle holds only the outline.

**Images and figures.** Product documentation carries much of its meaning in screenshots, diagrams and photos, and a text-only pipeline drops them silently. MarkItDown, the base converter, keeps a PDF's text and loses its images. So images are handled explicitly, at every stage:

1. **Extract.** Every embedded image is pulled out with where it sits and what describes it: its page, the section it belongs to, its caption, alt text and the text around it. That covers PDF figures, Word and PowerPoint media, a web page's `<img>` and `<figure>`, and standalone image files.
   - Docling (the `[docs]` extra) extracts PDF figures with captions and positions.
   - Office media and HTML images come from the files themselves.
   - Draw.io and SVG diagrams reuse docs-reader's existing text extraction.
2. **Filter out decoration.** Very small images (under 64 px on the short side, by default) and images repeated on many pages, such as logos and icons found by content hash, aren't kept as figures.
3. **Store.** Kept images are content-addressed assets: `asset://<sha256>.<ext>`, in the artifacts store with the uploaded originals.
   - EXIF metadata, including GPS position, is stripped on ingest.
   - In the bundle, the section that contains a figure gets a Markdown image in place: `![Figure 3: Replication topology](asset://9f2c….png)`, followed by its caption.
   - `assets: bundle` copies the images into the bundle's `assets/` directory instead. OKF treats non-`.md` files as assets. That's for small, image-light bases; large image sets belong outside git, or in Git LFS.
4. **Make them searchable, in three tiers:**
   - **Always, at no cost:** the caption, the alt text and the surrounding paragraph are indexed with the figure, so "the replication diagram" finds it.
   - **OCR, opt-in (`images: {ocr: true}`, with the `[docs]` extra):** text inside screenshots and diagrams, such as menu labels, error dialogs and box labels, is read and indexed under the figure. Scanned PDFs, where every page is an image, take the same path and are OCR'd whenever the extra is installed.
   - **Description, opt-in (`images: {describe: true, model: …}`):** a vision model writes a short factual description of each kept figure: what the diagram shows, which screen the screenshot is. It goes through `ModelProvider`, so it can be local (Ollama with a vision model) or a cloud model, and the build shows its estimated cost before it runs. Descriptions are cached by image hash, so a refresh doesn't pay for the same image twice. A description is stored under the figure as machine-generated text, labelled with the model that wrote it, so its trust is visible.
5. **Show them to agents and people.**
   - `fetch` returns a section's text with its figures as references: id, caption, page, description.
   - A new read tool, `kb_asset(id)`, returns the image itself as MCP image content, so an agent with vision can look at a screenshot rather than read about it.
   - `search` results say when a hit came from a figure's OCR text or description (`matched_in: figure`).
   - The portal shows figures inline in Browse, and a citation can point at a figure: "Figure 3, p. 42".

**Safety:** text read from an image is untrusted like any other source text.
- OCR output and descriptions go through the same ingest screening (§4).
- The description prompt treats the image as data to describe, never as instructions. An image can contain written instructions aimed at an AI, and the describing model is told to report them, not follow them.
- Per-image size and per-document image-count limits keep one huge PDF from swamping a build.

**What doesn't come in v1:**
- **Image search by visual similarity** (CLIP-style embeddings).
- **Page-image retrieval for visually dense documents** (the ColPali family, where each page is embedded as a picture).

Both are noted for phase 5. Captions, OCR and descriptions cover the common case, and they keep everything inspectable as text.

**Archives.** Documentation often arrives packed: a product's docs bundle, an exported help site, a Confluence or Notion space export, a vendor's PDF set. An archive is one source, and its contents are treated as if the folder had been uploaded.

- **Formats:** `.zip`, `.tar`, `.tar.gz` / `.tgz`, `.tar.bz2`, `.tar.xz`, and single-file `.gz`, all with the Python standard library. `.7z` and `.rar` need extra tools, so they're refused with a message saying to repack as `.zip`. They can be added later as optional converters.
- **Structure is kept.** The archive's folders become the bundle's folders (`sources/<archive-slug>/…`), so `index.md` shows the documentation the way it was organised. A single top-level folder, as most exports have, is flattened away.
- **Every member goes through the normal pipeline.** It's converted, split into sections if large, has its figures extracted, and is indexed. Members that can't be read (binaries, executables, fonts, unsupported formats) are listed as `unsupported` in the build, not silently ignored.
- **Exported sites work as sites.** An archive of HTML pages (a built MkDocs, Sphinx or Docusaurus site, a Confluence or Notion HTML export) keeps its internal links. A relative link from one page to another becomes an OKF link between their concepts, so the graph and `links` reflect the documentation's own cross-references.
  - Images referenced by the pages are taken from the archive.
  - Navigation chrome repeated on every page (menus, footers) is stripped by the HTML converter's main-content extraction.
  - `index.html` / `README` at the root becomes the archive's overview concept.
- **Nested archives** are expanded up to 2 levels deep. Deeper nesting is refused and named.
- **Updating is incremental.** Uploading a new version of the same archive to the same source compares its members by content hash: new and changed members are indexed, unchanged ones are skipped, and removed ones are deprecated after the prune window. Re-uploading a 2 GB docs bundle with three changed pages re-indexes three pages, and every other section keeps its review state.
- **Safety, checked before and during extraction:**
  - Paths are confined: absolute paths, `..` segments and symlinks or hardlinks pointing outside the archive are refused.
  - Device files are skipped.
  - **Decompression bombs** are caught: the total uncompressed size (default 4 GB), the member count (default 50,000), and the per-member compression ratio (default 200:1) are all capped, and extraction streams with the limits enforced as it goes, not trusting the archive's own header.
  - Encrypted members are skipped with `encrypted`.
  - Hidden files and OS clutter (`__MACOSX/`, `.DS_Store`, `Thumbs.db`) are skipped.
- **The original archive** is kept in the artifacts store like any upload, so a converter upgrade can rebuild from it.
- **On the CLI,** `swarmkit knowledge add handbook ./docs-export.zip` works the same way, and so does a source `url:` that points at an archive.

**Uploaded originals** go in the artifacts store, keyed by SHA-256 and named in the bundle as `upload://<sha256>/<filename>`. They're kept after conversion, so a converter upgrade can rebuild from them. Uploads count toward the base's storage figure in `knowledge show`. Deleting a base deletes its uploads after a 7-day grace period, which `knowledge restore` can undo.

**The index** is a cache at `.swarmkit/knowledge/<id>/index.sqlite`, gitignored, and always rebuildable from the bundle and sources:
- **Tables:** `concepts` (path, type, title, trust tier, status, stale, revision), `chunks` (concept, heading path, text, ordinal), FTS5 over chunks and concept titles, and `vec_chunks` (sqlite-vec) when embeddings are on.
- **The `meta` row** records the embedding model id, its dimension and the chunker version. A change to any of these forces a full re-embed, refused unless `--full` is passed, so vectors from two models never mix.
- **On Postgres:** a new `StoreKind.KNOWLEDGE` gives the same tables with `tsvector` and pgvector. It follows `storage.runtime` unless `storage.knowledge` is set, keeping the storage service's SQLite/Postgres parity.

**Serve-only deployments** have a writable workspace already (`ArtifactService` writes topologies there), so bundles live in the same place. A fleet instance's bundles are that instance's; distributing a base across a fleet is phase 4, through the control plane's existing artifact sync.

### 3. Declaration and grants

```yaml
knowledge:
  defaults: { search: auto, writes: review }
  bases:
    - id: handbook
      kind: okf
      description: On-call, escalation and release policy for the payments team.
      sources:
        - path: docs/**/*.md
        - upload: policies/
        - url: https://intranet.example/runbook
        - crawl: { url: https://docs.example.com/, depth: 2, max_pages: 200, include: ["/guides/*"] }
        - sitemap: https://docs.example.com/sitemap.xml
        - git: { url: https://github.com/acme/handbook, ref: main, paths: ["**/*.md"], credential: github }
        - skill: confluence-export
          args: { space: PAY }
      refresh: { every: 6h, prune: 7d }
      layout: { section_kb: 60, page_window: 10, materialize: full }   # full | outline
      images: { keep: true, ocr: false, describe: false, model: default, assets: store }   # assets: store | bundle
      redact: ['\bACCT-\d+\b']
      screen: auto             # off | auto (web, crawl and skill sources) | all
      enrich: { model: default, when: build }   # okf only: build | manual | off
      writes: review           # readonly | review | direct
      expose: { mcp: false, a2a: false, llms_txt: false, openai_search: false }
```

- **Schema:** a new `knowledge` block in `workspace.schema.json`, following `docs/notes/schema-change-discipline.md`. Ids are lowercase-kebab. `sources` is a list of single-key objects, one per source kind. `description` is required, because agents read it to decide whether to search the base.
- **Writes to `workspace.yaml`:** creating a base, deleting one, or changing its sources goes through `WorkspaceConfigService`, so it's validated and needs `admin`.
- **Grants:**
  - `pack:knowledge` grants the read skills for every base the caller may read; `knowledge:<id>` grants one base.
  - Either can be granted alongside `knowledge-propose`, the write skill, which is named separately and never carried by `pack:`, as with every write.
  - The resolver expands these into `mcp_tool` skills on the runtime-shipped server `knowledge`, the same way `pack:workspace` synthesizes agent skills today.
- **Harness executors** get the tools through the governed gateway like any MCP grant.

### 4. `KnowledgeService`, builds and refresh

`KnowledgeService` (`swarmkit_runtime/knowledge/_service.py`) is the only path; `test_service_layer_boundary.py` gains a check for it. Its operations:

- **Bases:** `list_bases`, `get_base`, `create_base`, `update_base`, `delete_base`. The last three go through `WorkspaceConfigService`.
- **Builds:** `add_sources`, `upload`, `build`, `refresh(full=False)`, `stop_build`, `resume_build`.
- **Reads:** `search`, `fetch`, `index`, `links`, `graph`.
- **Writes:** `propose`, `proposal_status`, `review` (`approve` / `reject`), `verify`, `edit` (a person's direct edit).
- **Portability:** `export`, `import_bundle`, `validate`, `doctor`.

**Builds are durable runs, not topology jobs.** A build isn't something a topology does, so it follows the `EvalService` pattern rather than `JobService`:
- **Storage:** a `knowledge_builds` row in the runtime store with status, per-source progress, counts and errors.
- **Liveness:** a heartbeat every 30 s, and a sweep at serve startup that marks stale rows `interrupted`.
- **Control:** stop takes effect between sources, and resume continues from the sources with no result.
- **Capacity:** a build on serve takes one slot of the job semaphore for its whole run, as an eval does.
- **Visibility:** the Jobs page lists builds beside runs, and its existing source filter gains a `knowledge` value.
- **Enrichment is different:** the `knowledge-builder` topology's work *is* a topology run, so it goes through `JobService` and appears as an ordinary job linked from the build.

**The build pipeline, per source:**
1. **Fetch.** Read the file, fetch the URL, crawl, clone or pull, or call the skill.
2. **Ledger check.** Skip a source whose content hash is unchanged; `--full` ignores the ledger.
3. **Convert.** Markdown and text pass through. With `[knowledge]` installed, MarkItDown handles Office, PDF and HTML; Docling (an opt-in extra) handles scanned PDFs. A converter can also be any skill that returns Markdown.
4. **Redact.** Apply the base's `redact` patterns and the workspace's `knowledge.defaults.redact`.
5. **Screen,** when `screen` applies. A decision skill (`knowledge-ingest-screen`, bundled) flags text addressed to an AI. A flagged source is held as `quarantined` until a person releases it.
6. **Write.** Write or update the Source concept, or the document's directory of section concepts for a large source (§2), with `generated` stamped and `status: draft`. Unchanged sections keep their revision, so a small edit to a manual doesn't reset the review status of every section. The runtime only ever writes a deterministic concept. Agents never do.
7. **Chunk.** Split on headings recursively, about 512 tokens with about 12% overlap, keeping the heading path.
8. **Index.** Update the full-text tables, and the vectors when embeddings are on.
9. **Finish.** Rewrite `index.md`, append to `log.md`, and record a `knowledge.built` audit event.

A source that fails is recorded with its reason and the build continues. Reasons are stable codes: `unsupported`, `too-large`, `duplicate`, `fetch-failed`, `convert-failed`, `quarantined`, `robots-disallowed`. The base can be searched from the first indexed source.

**Refresh** is incremental by content hash. `prune: 7d` deprecates a Source concept (`status: deprecated`) whose source has been gone for 7 days, rather than deleting it, so citations to it still resolve.
- **Scheduled refresh:** `refresh.every` runs from serve's `TriggerScheduler`, which gains a knowledge target beside its topology target. No trigger-schema change is needed, because the schedule is declared on the base.
- **On the CLI** there's no scheduler: `knowledge refresh` is manual, and `knowledge show` says when a refresh is overdue.

### 5. Governance: OKF trust, enforced

| Rule | Enforcement |
|---|---|
| Who wrote it | `generated.by` is **stamped by the service** from the authenticated actor: `human:<id>` from the portal or CLI session, `swarmkit/<agent-id>/<model>` for an agent, `swarmkit-knowledge/<version>` for the deterministic pass. Any `generated` in a proposal's content is ignored, and a warning names it. |
| Who checked it | `verified: [{by: human:<id>, at}]` is written only by `KnowledgeService.verify`. That needs the new **human-reserved scope `knowledge:verify`**, added to the reserved list beside `approvals:resolve` (invariant 6), so no agent can hold it whatever its grants. The id comes from the session, never from git config. |
| Self-verification | Refused: `verify` by the actor in `generated.by` is an error, not merely discounted (OKF issue #60). |
| A rewrite drops a check | Follows from OKF's "current verification" rule (only `verified.at >= generated.at` counts) together with stamping. The portal says so before a person saves an edit to a verified concept. |
| Agent writes | `propose(kb, changes, rationale, sources)` creates a **proposal**. A proposal holds a bundle diff (add, edit or deprecate concepts), the base revision it was made against, the proposer, and provenance (run id, trace link, source excerpts). It's stored in a `knowledge_proposals` table and reuses governed memory's quarantine states (`pending` → `approved` / `rejected`), and every transition is audited. |
| Approve vs verify | **Approve** publishes the proposal: the concepts are written with `generated.by` set to the proposer, unverified. **Verify** is separate. The portal and CLI offer "approve and verify" for a reviewer who has read the text, which writes both in one transaction. |
| Concurrency | A proposal records the revision (content hash) of each concept it touches. Approving one whose concepts have changed since is refused with `conflict`, and the reviewer sees both versions and can ask the agent to re-propose. Writes to a base hold a single-writer lock: a row lock on Postgres, a file lock on SQLite. |
| Undo | A published proposal can be reverted as a unit, which writes a new proposal restoring the previous revisions. |
| Write policy | `readonly`: no write skill is synthesized. `review` (default). `direct`: a proposal is approved automatically by the service, still unverified and still audited, for scratch bases. |
| Creating a base | Needs `admin` (a `workspace.yaml` change). An agent can only draft a `knowledge:` entry through the authoring flow, and a person applies it. |

**People's edits:** a person editing in the portal or CLI publishes directly, stamped `human:<id>`, with an option to verify in the same step. It's still audited and still appended to `log.md`.

### 6. Enrichment: `knowledge-builder` and `knowledge-answer`

**`knowledge-builder`** is a bundled reference topology for `okf` bases. It runs the LLM-wiki *ingest* and *lint* loops:
- reads new and changed Source concepts;
- drafts synthesized concepts with footnoted `sources`;
- links related concepts, and proposes `x-swarmkit-relations`;
- flags contradictions, stale claims, orphans and broken links;
- emits everything as **proposals**.

Its rules come from OKF's own `okf-generate` skill (never self-verify, never infer a human id, never write an attested query), imported with `swarmkit skill import`. Its cost appears in the build summary.

**`knowledge-answer`** is a bundled topology that answers in `okf-consume`'s format: the answer, then the concepts used with each one's trust tier, status and freshness, then citations, then gaps. It backs `swarmkit ask --knowledge`, the A2A skill `kb-<id>-qa`, and chat's `#base` scoping.

### 7. Front doors

**CLI** (`swarmkit knowledge …`, freed by #1134):
- create, add sources and build: `create`, `add`, `refresh`, `import`;
- read: `search --explain`, `show`, `graph`;
- review and trust: `review`, `verify`;
- share: `export --llms-txt --skill --tar`, `mcp`;
- diagnose: `doctor`;
- plus `swarmkit ask --knowledge <id>`.

`swarmkit init` offers "point me at a docs folder". `swarmkit validate` gains OKF conformance (frontmatter parses; reserved files follow spec §8–§9) and lint warnings: unsourced claims, `verified` without `human:`, self-verification, stale concepts, orphans, unquoted version strings, and `[[wikilinks]]`, which OKF doesn't count as links.

**Serve routes:**

| Route | Tier |
|---|---|
| `GET /knowledge`, `GET /knowledge/{id}`, `GET /knowledge/{id}/search`, `GET /knowledge/{id}/concepts/{path}`, `GET /knowledge/{id}/index[/{path}]`, `GET /knowledge/{id}/links/{path}`, `GET /knowledge/{id}/graph`, `GET /knowledge/{id}/builds[/{bid}]`, `GET /knowledge/{id}/proposals[/{pid}]`, `GET /knowledge/{id}/llms.txt` | read |
| `POST /knowledge/{id}/proposals` | run |
| `POST /api/knowledge`, `PUT`/`DELETE /api/knowledge/{id}`, `POST /api/knowledge/{id}/sources`, `DELETE /api/knowledge/{id}/sources/{n}`, `POST /api/knowledge/{id}/uploads` (multipart, chunked), `POST /api/knowledge/{id}/refresh`, `POST /api/knowledge/{id}/builds/{bid}/stop\|resume`, `POST /api/knowledge/{id}/proposals/{pid}/approve\|reject\|revert`, `PUT /api/knowledge/{id}/concepts/{path}` (a person's edit) | admin |
| `POST /api/knowledge/{id}/concepts/{path}/verify` | admin + `knowledge:verify` (human-reserved) |
| `POST /knowledge/preview-source` (a crawl or git preview: what would be fetched, sizes) | read |

Upload limits live in `server.knowledge`, with these defaults:
- `max_file_mb: 200` (a large product manual, with images)
- `max_request_mb: 2048`
- uploads are chunked and resumable, so a large file survives a dropped connection
- `allowed: [md, txt, html, pdf, docx, pptx, xlsx, csv, json, zip, tar, tar.gz, tgz, tar.bz2, tar.xz, gz]`
- archives: `max_uncompressed_gb: 4`, `max_members: 50000`, `max_ratio: 200`, `max_nesting: 2`

The limits are shown before a user uploads.

**The portal: a Knowledge page** (`/knowledge`, with query views like Evals, because the export is static).
- **The list:** each base's kind, size, last build, trust breakdown, stale and draft counts, pending proposals, and sources with errors.
- **The four-step creation flow (`/knowledge/?new=1`), as specified in #1127:**
  1. **Kind:** a card per kind with when to choose it, then the name, an id derived from it, and the description.
  2. **Sources:**
     - uploads: drag-and-drop files, a folder picker, or archives, with progress per file and the limits shown;
     - URLs, a site crawl with a preview, a sitemap or `llms.txt`;
     - a git repository, with a credential picker;
     - a server folder, path-confined;
     - connector skills, with accounts linked on the Connections page;
     - write or paste.

     Each row shows its estimated size, and unreadable files are named with what to install.
  3. **Settings,** with defaults and an "Advanced" section: search, converters, redaction, screening, enrichment and its estimated cost, refresh, write policy, exposure.
  4. **Review and create,** which leads to live build progress.
- **A base's page (`?kb=<id>`)** has tabs for Overview, Browse (trust badges, edit, verify), Search (with explain), Graph (React Flow), Sources, Review (proposal diffs, approve / reject / approve-and-verify / revert), Activity (`log.md` and audit), Connect (copy-paste setup for exposed interfaces) and Settings.
- **Elsewhere:** `#base` in chat, a Knowledge section in the topology composer's skills, and "Propose to a knowledge base" on a finished job's page.
- **Screenshots:** every portal PR attaches screenshots of every route and action, from a repeatable script against a real serve.

**Chat and authoring:** "make a knowledge base from our docs folder and the runbook site" goes to the authoring flow. It drafts the `knowledge:` entry, shows it, and a person applies it.

### 8. The read contract

The `knowledge` MCP server (stdio through `swarmkit knowledge mcp`, and over serve with OAuth):

| Tool | Shape |
|---|---|
| `kb_list()` | `[{id, kind, description, concepts, trust: {human_reviewed, machine_confirmed, unverified}, stale}]` |
| `search(query, kb?, filters?, current_only?, limit?)` | **ChatGPT's `search` shape:** `{results: [{id, title, url}]}`, extended per result with `kb`, `concept_id`, `heading_path`, `snippet`, `trust_tier`, `status`, `stale`, `score`. `url` is a portal permalink, so citations work. |
| `fetch(id)` | **ChatGPT's `fetch` shape:** `{id, title, text, url, metadata}`. `metadata` carries type, sources, `generated`, `verified`, tier, status, freshness and links. |
| `kb_index(kb, path?)` | That level's `index.md`, for progressive disclosure |
| `kb_asset(id)` | A figure or other asset, as MCP image content (with its caption, page and description), for agents with vision |
| `links(id, direction)` | Forward links and backlinks, typed when `x-swarmkit-relations` says so |
| `propose(kb, changes, rationale, sources)`, `proposal_status(id)` | Writes, when the base allows them. `propose` returns `resultType: "input_required"` (MCP 2026-07-28, SEP-2322) only when the client supports it; otherwise it returns `{proposal_id, status: "pending"}`. |

**Every read follows the same rules:**
- **Typed results:** results are typed `structuredContent` plus the JSON text copy.
- **Retrieved text is data:** it never appears in a tool description.
- **Per-caller caching:** results filtered for one caller are marked `cacheScope: private`.
- **Audit:** every `search` records a `knowledge.searched` event.
- **Unused grants are flagged:** a run that was granted knowledge and never searched it is flagged in its summary.

**Inside SwarmKit:** an optional `knowledge-reader` pre-input binding injects a delimited `<knowledge>` block, mirroring `memory-reader`. Tools stay the default.

### 9. Adapters for other stores

An adapter is a data file under `swarmkit_runtime/knowledge/adapters/<kind>.yaml`, a bundled library like the harness adapters. It names the catalogue bundle that provides the MCP server, and maps each contract operation to a tool with an argument template and a result mapping:

```yaml
apiVersion: swarmkit/v1
kind: KnowledgeAdapter
metadata: { id: gbrain, name: GBrain }
spec:
  bundle: gbrain                       # swarmkit-skills catalogue entry
  search: { tool: search_memory, args: { query: "{query}", limit: "{limit}" }, results: "$.results[*]", map: { id: "$.slug", title: "$.title", snippet: "$.excerpt" } }
  fetch:  { tool: recall, args: { slug: "{id}" }, map: { text: "$.content" } }
  links:  { tool: get_links, args: { slug: "{id}" } }
  write:  { tool: save_memory, args: { slug: "{id}", content: "{text}" } }   # called only on an approved proposal
```

- **What SwarmKit adds:** the read contract, per-caller access before retrieval, governed proposals (the write tool is called only on approval), audit, and the same exposure as native bases.
- **Unauthenticated servers** (Graphiti's reference server, for one) run as local stdio children and are never exposed directly.
- **Setup:** `swarmkit knowledge create brain --kind gbrain` installs the bundle and writes the `mcp_servers` entry plus the `knowledge:` entry in one step.
- **Keeping names current:** tool names are checked against the live server by the catalogue's nightly liveness check, because these projects rename tools (Basic Memory recently did).
- **Exporting:** an adapter can export approved facts as OKF concepts, giving graph and memory stores a portable, reviewable copy.

### 10. Exposure

| Interface | Default |
|---|---|
| The `knowledge` MCP server to this workspace's agents | on |
| The `knowledge` MCP server to external clients (stdio, or serve with OAuth 2.1 / PKCE, audience-bound tokens) | off per base |
| A2A: `kb-<id>-qa` per base, answers as Task artifacts, private bases only on the authenticated extended card | off |
| `GET /knowledge/{id}/llms.txt` and `llms-full.txt` | off |
| `SKILL.md` export (how a coding agent should use the base) | on demand |
| `POST /v1/vector_stores/{id}/search` (OpenAI's request and response shape) | off |
| An OKF bundle (`export --tar`, or the git directory) | always |

### 11. Defaults

| Concern | Base install | `[knowledge]` extra | Opt-in |
|---|---|---|---|
| Convert | Markdown, text | MarkItDown (Office, PDF, HTML) | Docling (CPU), Kreuzberg/Xberg, any converter skill |
| Search | Keyword (FTS5 / `tsvector`) | Hybrid: FTS5 + fastembed `bge-small-en-v1.5` (67 MB, MIT, CPU) via sqlite-vec, combined with RRF (k = 60), top 8 | Rerank the top 30 (MiniLM-L6, CPU); contextual retrieval |
| Embeddings | none | fastembed, behind a new `ModelProvider.embed()` | Ollama, OpenAI, Voyage, Gemini through `embed()` |

- **Why keyword-only is the base default:** it works offline with no download. Every surface that searches (`show`, `doctor`, the portal's Search tab) says "meaning-based search is off: install `swarmkit-runtime[knowledge]`".
- **Licences:** GPU-only and AGPL components stay out of every default: Marker, MinerU, PyMuPDF4LLM, and self-hosted Firecrawl.
- **Embeddings in one place:** `ModelProvider.embed()` also absorbs the three embedding paths that exist today (drift detection, governed memory, workspace memory). Invariant 4 says every model call goes through `ModelProvider`, and `drift/_embeddings.py` imports sentence-transformers directly.

### 12. Security

- **Retrieved text is untrusted.** It's returned as data and never placed in tool descriptions or system prompts, the same rule as eval capture's reference judge.
- **Agent writes are where injections would persist,** so the review gate (§5) is a security control.
- **Ingest screening** (§4) covers web, crawl and connector sources by default.
- **Access is per caller and checked before retrieval.** `kb_list` and `search` never reveal a base the caller can't read.
- **Reads are path-confined to the bundle,** enforcing OKF's "no reads outside the bundle root" rule, the same approach as docs-reader.
- **Crawls stay where they're pointed:** robots.txt is respected; crawls stay same-site by default; private and loopback addresses are refused unless `allow_private: true`, to prevent SSRF.
- **Git URLs** are checked against `server.knowledge.allowed_git_hosts`, when set.

## OKF tracking

- **Reading:** v0.1–v0.3 leniently, using the spec's fallbacks (§12.1, §13.1).
- **Writing:** v0.3 only.
- **Validation:** SwarmKit ships its own validator, because upstream has none.
- **Upstream contributions:** offer `x-swarmkit-relations` as input to typed relations (OKF #16), and the strict self-verification rule (#60).
- **When the next breaking minor version lands:** the bundles SwarmKit writes keep validating under the version they declare (`okf_version`), and a `knowledge migrate` command upgrades them on request. Nothing is rewritten silently.
- **The risk:** OKF has two committers, both at Google, and two of its three releases broke compatibility. Typed relations, stable ids and refutation are deferred.

## Implementation plan

| Phase | PRs | Exit demo |
|---|---|---|
| **0. Design and cleanup** | This note. #1133: `search_knowledge` through `governed_mcp_call`, test first. #1134: `docs-server` / `docs-pack`, with aliases. | Design merged |
| **1. Read-only RAG MVP** | 1a schema (`knowledge` block, schema minor). 1b `KnowledgeService`, `knowledge_builds`, the deterministic pass (Markdown and text), FTS5 index, CLI `create / add / refresh / search / show / import / doctor`. 1c the `knowledge` MCP server (read tools), `pack:knowledge` and `knowledge:<id>`, `swarmkit ask --knowledge`, OKF checks in `validate`. 1d MarkItDown under `[knowledge]`, uploads, URL and git sources; figures extracted, stored and indexed by caption and alt text, shown in Browse, returned by `fetch` and `kb_asset`. 1e serve routes and the portal (list, creation flow for uploads, URL, git and folder, build progress, Browse, Search). | `just demo-knowledge`: a folder to a cited answer in three commands, and the same base queried from Claude Code over MCP; portal screenshots of creating a base from uploads |
| **2. Governed writes and curated bases** | Proposals, review, revert; the `knowledge:verify` reserved scope; stamping; `log.md` and audit; portal Review, Activity, Graph, editing; `knowledge-builder` and `knowledge-answer`; crawl, sitemap and connector sources; ingest screening. | An agent proposes, a person approves and verifies in the portal, and an agent rewrite drops the tier to unverified |
| **3. Retrieval quality** | `ModelProvider.embed()` and consolidation; fastembed + sqlite-vec + pgvector, RRF; rerank; Docling, with figure OCR and opt-in vision descriptions; contextual retrieval; `--explain`; "never reached" warnings; knowledge eval cases through eval capture. | The tutorial's paraphrase question answered; keyword vs hybrid compared on captured eval cases |
| **4. Other stores and exposure** | The adapter library and governed write proxy; "connect an existing store" in the portal; A2A `kb-<id>-qa`; `export --llms-txt --skill`; OpenAI-compatible search; the Connect tab; fleet distribution. | The Sterling workspace on `knowledge:` with far fewer hand-written lines; ChatGPT citing portal permalinks |
| **5. Later** | Memory facts proposed into a base; Attested Computations; per-folder ACLs; signed bundles. | Separate notes |

Each phase gets its own issue under #1127. Phases 1 and 2 bump `swarmkit-schema` and `swarmkit-webui`, with the `[ui]` floor raised to match, and every phase bumps `swarmkit-runtime`.

## Test plan (phase 1; later phases add their own)

- **Schema:** valid and invalid `knowledge` blocks (unknown key, non-kebab id, missing description, a source with two keys). Codegen drift is clean.
- **Build:**
  - A folder of Markdown and text gives one Source concept per small file, `index.md` and `log.md`, and a conformant bundle by SwarmKit's validator and OKF's conformance rules.
  - A large document becomes a directory: an overview concept plus section concepts split at headings, each within `section_kb`. A document with no headings is split into page windows, and section citations carry page numbers.
  - Every word of a large source is searchable, in both `materialize` modes.
  - Figures in a PDF, a Word file and a web page are extracted with captions, pages and sections. Decorative and repeated images are dropped. EXIF metadata is stripped.
  - A figure is found by its caption, and by its OCR text when OCR is on. `kb_asset` returns the image. A described image carries its describing model's name.
  - Editing one section of a large document re-indexes that section only, and the other sections keep their revision and any verification.
  - Unchanged sources are skipped on refresh; a changed one is re-indexed.
  - A deleted source is deprecated after the prune window, not removed.
  - An unsupported file is reported as `unsupported`, and the build continues.
  - A stopped build resumes from the sources with no result. A build left running by a dead process is swept as `interrupted` at startup.
- **Index:**
  - Search ranks by FTS5 and returns heading paths.
  - A changed chunker version without `--full` is refused.
  - The index rebuilds from the bundle alone. It works on SQLite and on Postgres (`SWARMKIT_TEST_POSTGRES_URL`).
- **Contract:**
  - `search` and `fetch` validate against ChatGPT's schemas.
  - Results carry the trust tier and status; `url` is a permalink.
  - A base the caller can't read is absent from `kb_list` and `search`.
  - Reads outside the bundle are refused.
- **Grants:**
  - `pack:knowledge` expands to the read skills of every base.
  - `knowledge:<id>` gives one base.
  - `knowledge-propose` is never carried by `pack:`.
- **Doors:** the CLI, serve, the MCP server and `swarmkit ask --knowledge` give the same results from the same base.
- **Boundary:** the service-layer boundary test finds no knowledge path around `KnowledgeService`.
- **Uploads:** limits are enforced with a 413 naming the limit; a re-uploaded unchanged file is a no-op.
- **Archives:**
  - `.zip` and `.tar.*` expand with their folder structure kept;
  - an HTML site export's relative links become concept links;
  - a re-uploaded archive with three changed members re-indexes three;
  - path traversal, links pointing outside the archive, and a crafted decompression bomb (over the size, count or ratio caps) are each refused with a named reason, and nothing is written outside the base;
  - `.7z` is refused with the "repack as .zip" message;
  - nested archives deeper than 2 levels are refused.

## Demo plan (phase 1)

`just demo-knowledge`, with no key and no network:
1. `swarmkit knowledge create handbook --from examples/knowledge/handbook/`: the build's progress, then `show`.
2. `swarmkit ask --knowledge handbook "Who do I page if nobody answers?"`: a cited answer with trust tiers, on the mock provider with a scripted answer.
3. `swarmkit knowledge search handbook "escalation" --explain`.
4. Edit one file and run `refresh`: one source re-indexed, the rest skipped.
5. Start `swarmkit knowledge mcp handbook` and call `search` and `fetch` from a test MCP client, as Claude Code would.

## Open questions (with the proposals above as the defaults)

1. **Hybrid search by default,** despite a 67 MB download on the first build with `[knowledge]`? Proposed: on with the extra, off in the base install.
2. **Section size and the page-window fallback.** Proposed: `section_kb: 60` and 10-page windows, measured on Sterling's PDFs and one public product manual in phase 1.
3. **Connector sources in phase 2 vs phase 1.** Proposed: phase 2, because they need the screening default.
4. **Whether `direct` writes should exist at all.** Proposed: yes, for scratch bases, always unverified and audited.
