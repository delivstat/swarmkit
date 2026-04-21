# SwarmKit

> An open-source framework for composing, running, and growing multi-agent AI swarms.

SwarmKit treats swarm topology — who exists, who reports to whom, what skills they can exercise — as declarative data rather than imperative code. This separation lets non-developers compose agent teams conversationally while developers retain full programmatic control. Swarms are not static: every swarm can observe its own capability gaps and grow new skills through a conversational, human-approved authoring flow.

**Status:** pre-v1.0. Milestone 0 (schema foundation) is complete — all five canonical JSON Schemas plus generated pydantic models and TypeScript types are in place and exercised by a cross-language demo. Milestone 1 (topology loading & resolution) is next. See [`design/IMPLEMENTATION-PLAN.md`](./design/IMPLEMENTATION-PLAN.md) for the roadmap; [`design/SwarmKit-Design-v0.6.md`](./design/SwarmKit-Design-v0.6.md) is the authoritative architecture.

## Monorepo layout

```
swarmkit/
├── design/              # Authoritative architecture (v0.6 markdown; see also IMPLEMENTATION-PLAN.md)
├── packages/
│   ├── runtime/         # swarmkit-runtime — Python CLI + HTTP server, LangGraph compiler, AGT wiring
│   ├── schema/          # swarmkit-schema — canonical JSON Schemas + Python & TypeScript validators + generated models/types
│   │   ├── schemas/     #   canonical JSON Schema files (source of truth for everything)
│   │   ├── python/      #   Python validator + generated pydantic models
│   │   └── typescript/  #   TypeScript validator + generated TS types
│   └── ui/              # swarmkit-ui — Next.js composer, authoring interface, runtime dashboard (v1.1)
├── reference/           # v1.0 reference artifacts (topologies, archetypes, skills — populated M6+)
├── docs/                # User-facing documentation + cross-cutting discipline notes
├── scripts/             # Dev scripts (codegen, demos)
├── llms.txt             # LLM-queryable index of the corpus (llmstxt.org)
└── .claude/             # Claude Code config (agents, commands, settings)
```

The three packages share the topology and skill file formats but do **not** depend on each other at runtime — per design §9.2, each can operate standalone.

## Getting started

Prereqs: `python >= 3.11`, `node >= 20`, `pnpm >= 9`, `uv`, `just`.

```bash
just install     # uv sync + pnpm install
just lint        # ruff + biome
just typecheck   # mypy + tsc
just test        # pytest + vitest
```

See [`justfile`](./justfile) for all tasks.

### Milestone 0 exit demo — schemas end-to-end

With install complete, exercise every schema in both languages:

```bash
just demo-schema
```

The target loads every valid + invalid fixture under `packages/schema/tests/fixtures/<artifact>/` through both `swarmkit_schema.validate()` (Python) and `@swarmkit/schema.validate()` (TypeScript), prints a pass/fail table, and exits non-zero on any mismatch. It covers all five canonical artifact types: topology, skill, archetype, workspace, trigger.

Additional demos:

```bash
just demo-topology-schema       # just one artifact kind
just demo-codegen               # load fixtures through generated pydantic + TS types
just schema-codegen             # regenerate pydantic + TS types from the schemas
just schema-codegen-check       # drift detection (runs in CI)
```

## Try it as an LLM can

SwarmKit is designed so any LLM can understand and query the framework. The repo ships an [`llms.txt`](./llms.txt) at the root (per [llmstxt.org](https://llmstxt.org)) that indexes the design doc, schemas, examples, and discipline notes. Point any LLM at it — or drop the contents of [`design/SwarmKit-Design-v0.6.md`](./design/SwarmKit-Design-v0.6.md) plus [`packages/schema/schemas/`](./packages/schema/schemas/) into a chat — and the LLM can answer questions about how SwarmKit works with primary-source accuracy.

The forthcoming `swarmkit knowledge-pack` CLI (task #24) will bundle this automatically, including your current workspace state.

## Packages

| Package | Language | Distribution | Status |
| --- | --- | --- | --- |
| [`swarmkit-runtime`](./packages/runtime) | Python 3.11+ | PyPI + Docker | scaffolded; M1 implementation next |
| [`swarmkit-schema`](./packages/schema) | Python + TypeScript | PyPI + npm | **M0 complete** — schemas + validators + generated models + types + drift-protected codegen |
| [`swarmkit-ui`](./packages/ui) | TypeScript / Next.js | npm + hosted | scaffolded; v1.1 |

## Design principles

From §7 of the design doc:

- **Topology as data, not code.** Swarms are YAML/JSON, interpreted at runtime.
- **Skills as the only extension primitive.** Capability, decision, coordination, persistence — one surface.
- **Framework-aligned, not framework-locked.** LangGraph is the v1.0 engine; the schema is portable.
- **Trust boundaries as first-class concept.** Communication patterns are categorised by trust zone.
- **Governance built in, not bolted on.** Separation of Powers model, implemented on Microsoft AGT.
- **Growth through human-approved authoring.** Swarms surface gaps; humans decide.
- **Eject, never lock in.** `swarmkit eject` exports the LangGraph code at any time.

## License

MIT — see [LICENSE](./LICENSE).
