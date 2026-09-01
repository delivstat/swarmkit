# CLAUDE.md

Primary project reference for Claude Code instances working in this repo. Read this first; it's a map, not a tutorial.

## What SwarmKit is

An open-source framework for composing, running, and **growing** multi-agent AI swarms. Three distinctive claims, in order of importance:

1. **Topology is data.** Swarms are YAML/JSON files the runtime interprets. Not Python code.
2. **Skills are the only extension primitive.** Capability / decision / coordination / persistence — one mental model.
3. **Swarms grow through human-approved authoring.** Gap detection → surface → author → test → publish, gated at every step.

The authoritative architecture lives in `design/SwarmKit-Design-v0.6.md` — markdown, canonical, LLM-queryable. Read that when answering "what did the design say about X?" (The original `.docx` is archived under `design/archive/` as historical reference only; it is not authoritative and is not updated.)

**Status:** shipping. `swarmkit-runtime` is at **1.200.0** on PyPI (1.0.0 was 2026-04-26); `swarmkit-schema`, `swarmkit-webui` and `swarmkit-control-plane` version independently. Phases 1–5 of `design/IMPLEMENTATION-PLAN.md` are complete, ~2,850 tests run in CI, and the design doc is now a record of *why*, not a plan of *what next* — where the two disagree, the code is what shipped.

One deliberate reversal is worth knowing before you read the design doc: **the bundled pipeline layer was removed in 1.189.0** (`kind: StageGraph`, the saga controller, `swarmkit orchestrator`, `swarmkit pipeline`, `POST /pipelines/*`). Sequencing belongs to the calling application; `examples/pipeline-orchestrator/` is the reference application, and it imports no runtime module. The design doc and `design/IMPLEMENTATION-PLAN.md` still describe M21 as shipped — that is history, not current state.

## Repository shape

Polyglot monorepo. Python runtime + TS UI + dual-language schema.

```
swarmkit/
├── design/              # Architecture docs (v0.6 current) — source of truth for decisions
├── packages/
│   ├── runtime/         # Python: topology interpreter, LangGraph compiler, AGT wiring, CLI, HTTP server
│   ├── schema/          # Canonical JSON Schemas + Python & TS validators
│   ├── ui/              # Next.js: the workspace portal — composer, topology canvas, gates, audit
│   ├── webui/           # Python package that SHIPS the built portal; `_static/` is generated, not committed
│   ├── control-plane/   # Python: the fleet control plane (self-hostable, independent of the runtime)
│   └── control-plane-ui/ # Next.js: the fleet panel
├── reference/           # Reference topologies, archetypes, skills, command packs (YAML)
├── examples/            # Runnable examples + `just demo-*` targets
├── docs/                # User-facing docs + cross-cutting notes
│   └── notes/           # Discipline / gotcha / "don't forget" notes (schema-change-discipline.md, etc.)
├── deploy/ · docker/    # Compose stacks and images
├── scripts/             # Dev scripts (codegen, release guard, changelog)
├── .claude/             # Claude Code config
└── justfile             # Cross-language task runner
```

`packages/runtime`, `packages/ui` and `packages/schema` each have their own `CLAUDE.md` with package-specific invariants and style — read those when working in a subdirectory. `webui`, `control-plane` and `control-plane-ui` do not have one yet.

**Before making a cross-cutting change, check `docs/notes/` for a matching discipline note** (e.g. schema changes: `docs/notes/schema-change-discipline.md`). If your change introduces a new "remember to also touch Y when you change X" rule, add a note.

## Tooling

| Layer | Tool |
| --- | --- |
| Python env + build | `uv` workspace (`packages/runtime`, `packages/schema/python`, `packages/control-plane`, `packages/webui`) |
| Python lint / format | `ruff` |
| Python typecheck | `mypy --strict` |
| Python test | `pytest` + `pytest-asyncio` |
| JS/TS package mgmt | `pnpm` workspace (`packages/ui`, `packages/control-plane-ui`, `packages/schema/typescript`) |
| JS/TS lint / format | `biome` |
| JS/TS typecheck | `tsc --noEmit` |
| JS/TS test | `vitest` |
| Task orchestration | `just` (bridges Python + JS) |

Run `just` with no args to list tasks. Common ones:

```bash
just install     # uv sync + pnpm install
just lint        # ruff + biome
just typecheck   # mypy + tsc
just test        # pytest + vitest
just build       # uv build + pnpm build
```

**Verify the way CI does, which is repo-wide.** CI runs `ruff check .`, `ruff format --check .` and `mypy packages/runtime packages/schema/python packages/control-plane` — the last one **includes tests**. Checking only the package you touched passes locally and fails on the PR; that has happened twice. `mypy` reads `strict = true` from the root `pyproject.toml`, so the flag is implied.

## Reading the design before changing anything

The design doc is detailed and opinionated. Before making a non-trivial change, find the relevant section(s):

- **§5–§6** — mental model: topology / agent / archetype / skill
- **§7** — architectural principles (these are tie-breakers)
- **§8** — Separation of Powers governance model (legislative / executive / judicial / media)
- **§9** — three-component system (runtime, UI, schema)
- **§10** — topology schema (high-level)
- **§12** — skill authoring / swarm evolution
- **§14** — runtime architecture + CLI entry points
- **§18** — MCP integration
- **§21** — open questions still pending decision

## Non-negotiable invariants

These hold across the whole repo. Individual package `CLAUDE.md`s add more.

1. **Topology-as-data, always.** No generating Python as the output of a "topology compiler" — we interpret. Swarms stay portable, open data (YAML/JSON), never generated code.
2. **Skills are the only *capability* extension primitive.** Four backings, one noun: `mcp_tool`, `llm_prompt`, `composed`, and `command` (a local binary from a workspace `command_packs` entry — `design/details/command-packs.md`). When tempted to add a parallel *capability* mechanism, ask how it could be a skill category, a composed skill or a command pack instead. MCP was never the paradigm; skills were. *Node execution* is a separate provider seam — the `executor` abstraction (`design/details/executor-abstraction.md`), where `model` and `harness` are kinds — parallel to `ModelProvider`/`GovernanceProvider`, not a capability primitive. (A session-holding, diff-producing harness is an executor; a stateless answer-and-return consult is still a skill.)
3. **All governance goes through the `GovernanceProvider` interface** (design §8.5). Only `packages/runtime/src/swarmkit_runtime/governance/` imports AGT directly.
4. **All LLM calls go through the `ModelProvider` interface** (`design/details/model-provider-abstraction.md`). Only `packages/runtime/src/swarmkit_runtime/model_providers/` imports `anthropic` / `openai` / `google-genai` / Ollama's HTTP client. Same shape as the governance rule; same reasoning — no vendor lock-in at framework level.
5. **Audit log is append-only from executive perspective.** No update/delete path exposed to agents, ever (design §8.3, §8.7).
6. **Human approval gates are structural, not prompt-suggested.** Scopes reserved for human identity (`skills:activate`, `mcp_servers:deploy`, `topologies:modify`, `iam:modify`, `approvals:resolve`) are enforced by the policy engine — no agent can be granted them regardless of prompt (design §8.7).
7. **Topologies stay portable, open data.** Swarms are interpretable YAML/JSON any conformant runtime can run — the no-lock-in guarantee is the openness of the artifacts plus the OSS runtime, not a code-export escape hatch. (`swarmkit eject` was dropped: as the runtime grew, features stopped being expressible as standalone generated LangGraph code.)
8. **The portal ships with the runtime, same origin.** `pip install "swarmkit-runtime[ui]"` + `swarmkit serve` hosts the built Next.js export out of `swarmkit-webui`; there is no separate deployment. The `[ui]` extra is **floored**, not open — a portal built against a removed API must fail at install time rather than in a browser. `_static/` is generated by the publish workflow and is gitignored: never commit it, and never hand-edit it. (This invariant used to read "v1.0 UI is deferred, do not add UI features" — superseded by `design/details/serve-hosted-webui.md`, `design/details/workspace-ui.md` and the M20 canvas, all shipped.)

## Style

- **Python:** 3.11+, strict typing, `pydantic` for schema-shaped data, async-first for I/O, no bare `raise Exception`.
- **TypeScript:** ES2022 target, strict mode with `noUncheckedIndexedAccess`, no default exports for library code, `biome` for formatting.
- **YAML:** 2-space indent, lowercase-kebab IDs (pattern `^[a-z][a-z0-9-]*$`), `apiVersion: swarmkit/v1` at top of every artifact.
- **Markdown:** sentence case in headings, fenced code blocks with language tags, link to design sections by number not title.

## Feature delivery workflow — MANDATORY

Every feature, big or small, follows this lifecycle. No exceptions.

**Branch protection is enforced on `main`.** Direct pushes are blocked for all contributors including admins. Every change — code, docs, workspace files, design notes — goes through a pull request.

1. **Design first.** Write a short design note at `design/details/<feature-slug>.md` — or, if the v0.6 doc already covers it, reference the section. Must state: goal, non-goals, API shape, test plan, demo plan. For large features open a design-only PR and get review before implementation.
2. **Branch.** `feat/<scope>-<slug>`, `fix/<scope>-<slug>`, `design/<slug>`, `docs/<slug>`, etc. One feature per branch.
3. **Tests are mandatory.** Every feature ships with tests — unit, integration, or both as appropriate. No "tests later." A PR without tests is not reviewable.
4. **Demo every feature.** Every feature — small or big — ships a demo: a runnable script under `examples/`, a recorded terminal transcript in the PR body, a screenshot for UI changes, or a `just demo-<feature>` target. The demo proves the feature works for a real user, not just the compiler.
5. **PR against `main`.** The PR description must link the design note, show the demo, and summarise test coverage. Use the template at `.github/pull_request_template.md`.
6. **Review + green CI + merge.** Delete the branch after merge.

This applies to this CLAUDE.md change too — these rules arrived via `feat/workflow-rules-and-plan`.

## Commit and PR style

Conventional-ish: `type(scope): subject`. Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `design`. Scopes are package names (`runtime`, `schema`, `ui`) or areas (`topology`, `skills`, `governance`, `cli`).

Examples:
- `feat(runtime): load topology YAML and validate against schema`
- `design(v0.7): resolve §21 sandboxing question`
- `chore(workspace): bump biome to 1.10`

**Authorship:** commit messages and PR bodies carry **delivstat authorship only**. Do not add `Co-Authored-By:` trailers. Do not add `🤖 Generated with …` footers. The body is subject + substance; nothing else.

## Implementation plan

`design/IMPLEMENTATION-PLAN.md` tracks the phased roadmap to v1.0. Each milestone lists its features; each feature becomes its own design note + PR. Update the plan as features land (check off items, adjust ordering) — the plan is a living document.

## When in doubt

- Read the relevant section of `design/SwarmKit-Design-v0.6.md`.
- If the design is silent or contradictory, it's an open question — flag it in `design/` rather than deciding unilaterally.
- The three pillars of the product story (topology-as-data, skills-as-extension, growth-through-authoring) are tie-breakers for architectural calls.

## Release checklist

When cutting a release:

1. **Bump versions** for every package whose content changed — not just the runtime. `just
   release-check` fails the release when a changed package's version is already on PyPI, because
   `publish_if_new` would silently skip it. Versions live in `packages/runtime/pyproject.toml`,
   `packages/schema/python/pyproject.toml`, `packages/webui/pyproject.toml` and
   `packages/control-plane/pyproject.toml`.

   **It has caught this three times and missed it twice, and both misses shipped:**
   `swarmkit-schema` froze at 1.23.0 across six releases, so a published runtime rejected artifacts
   its own schema had been extended to accept; and `swarmkit-webui` froze at 0.14.0 for three weeks,
   so the published portal kept a Pipelines section calling an API removed in 1.189.0 — found by a
   user, not by CI. Both misses had one cause: the guard compared each package against the **last
   tag**, and a version frozen across several releases stops looking changed. It now compares
   against the commit that last *set* the version, and watches each package's own `pyproject.toml`
   because dependencies and extras ship in the wheel metadata. See
   `docs/notes/release-version-discipline.md`.
2. **Commit** the version bump. The guard reads committed history, so it cannot see a working-tree
   edit — run it after the commit, not before.
3. **Build** all four packages: `uv build --all-packages` — verify each succeeds, and check the
   wheel actually carries what you think it does (`zipfile` over the `.whl` beats trusting the
   workflow).
4. **Tag**: `git tag -a v1.x.y -m "SwarmKit v1.x.y — summary"`.
5. **Push**: `git push origin v1.x.y` — triggers PyPI publish + Docker build via GitHub Actions.
6. **Regenerate the changelog**: `just changelog` reads the annotated tags and rewrites
   `docs/site/releases/changelog.md`. It only sees tags that exist, so this runs *after* the push,
   in a follow-up docs PR. The document claimed to be tag-generated for months while being
   hand-maintained, and drifted 33 versions behind — the tag subject is the summary, so write it
   like one.

PyPI does not allow re-uploading the same version. If the tag is pushed before the version bump, the publish fails and requires deleting the tag, bumping, and re-tagging. Always bump first.
