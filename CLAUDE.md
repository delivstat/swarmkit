# CLAUDE.md

Primary project reference for Claude Code instances working in this repo. Read this first; it's a map, not a tutorial.

## What SwarmKit is

An open-source framework for composing, running, and **growing** multi-agent AI swarms. Three distinctive claims, in order of importance:

1. **Topology is data.** Swarms are YAML/JSON files the runtime interprets. Not Python code.
2. **Skills are the only extension primitive.** Capability / decision / coordination / persistence — one mental model.
3. **Swarms grow through human-approved authoring.** Gap detection → surface → author → test → publish, gated at every step.

The authoritative architecture lives in `design/SwarmKit-Design-v0.6.docx`. A plain-text extraction is at `design/SwarmKit-Design-v0.6.extracted.md` — read that when answering "what did the design say about X?"

**Status:** pre-v1.0. Design is at v0.6 and approved-in-principle. Phase 1 implementation (13–16 weeks per §20.1) has not started. This repo is currently scaffolding only.

## Repository shape

Polyglot monorepo. Python runtime + TS UI + dual-language schema.

```
swarmkit/
├── design/              # Architecture docs (v0.6 current) — source of truth for decisions
├── packages/
│   ├── runtime/         # Python: topology interpreter, LangGraph compiler, AGT wiring, CLI
│   ├── schema/          # Canonical JSON Schemas + Python & TS validators
│   └── ui/              # Next.js: composer, authoring interface, dashboard (v1.1)
├── reference/           # v1.0 reference topologies, archetypes, skills (YAML)
├── docs/                # User-facing docs
├── scripts/             # Dev scripts
├── .claude/             # Claude Code config
└── justfile             # Cross-language task runner
```

Each package has its own `CLAUDE.md` with package-specific invariants and style. Read those when working in a subdirectory.

## Tooling

| Layer | Tool |
| --- | --- |
| Python env + build | `uv` workspace (members under `packages/runtime`, `packages/schema/python`) |
| Python lint / format | `ruff` |
| Python typecheck | `mypy --strict` |
| Python test | `pytest` + `pytest-asyncio` |
| JS/TS package mgmt | `pnpm` workspace (`packages/ui`, `packages/schema/typescript`) |
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

1. **Topology-as-data, always.** No generating Python as the output of a "topology compiler" — we interpret. `swarmkit eject` is the one path that produces code, and it's a user-facing export, not a runtime mechanism.
2. **Skills are the only extension primitive.** When tempted to add a parallel extension mechanism, ask how it could be a skill category or a composed skill instead.
3. **All governance goes through the `GovernanceProvider` interface** (design §8.5). Only `packages/runtime/src/swarmkit_runtime/governance/` imports AGT directly.
4. **Audit log is append-only from executive perspective.** No update/delete path exposed to agents, ever (design §8.3, §8.7).
5. **Human approval gates are structural, not prompt-suggested.** Scopes reserved for human identity (`skills:activate`, `mcp_servers:deploy`, `topologies:modify`, `iam:modify`) are enforced by the policy engine — no agent can be granted them regardless of prompt (design §8.7).
6. **Eject must stay intact.** Any runtime feature needs an ejection story — if it can't be expressed in generated LangGraph code, reconsider.
7. **v1.0 UI is deferred.** CLI chat mode is the v1.0 on-ramp. Do not add UI features before the design question in §15.3 is re-confirmed.

## Style

- **Python:** 3.11+, strict typing, `pydantic` for schema-shaped data, async-first for I/O, no bare `raise Exception`.
- **TypeScript:** ES2022 target, strict mode with `noUncheckedIndexedAccess`, no default exports for library code, `biome` for formatting.
- **YAML:** 2-space indent, lowercase-kebab IDs (pattern `^[a-z][a-z0-9-]*$`), `apiVersion: swarmkit/v1` at top of every artifact.
- **Markdown:** sentence case in headings, fenced code blocks with language tags, link to design sections by number not title.

## Commit style

Conventional-ish: `type(scope): subject`. Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `design`. Scopes are package names (`runtime`, `schema`, `ui`) or areas (`topology`, `skills`, `governance`, `cli`).

Examples:
- `feat(runtime): load topology YAML and validate against schema`
- `design(v0.7): resolve §21 sandboxing question`
- `chore(workspace): bump biome to 1.10`

## When in doubt

- Read the relevant section of `design/SwarmKit-Design-v0.6.extracted.md`.
- If the design is silent or contradictory, it's an open question — flag it in `design/` rather than deciding unilaterally.
- The three pillars of the product story (topology-as-data, skills-as-extension, growth-through-authoring) are tie-breakers for architectural calls.
