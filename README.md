# SwarmKit

> An open-source framework for composing, running, and growing multi-agent AI swarms.

SwarmKit treats swarm topology — who exists, who reports to whom, what skills they can exercise — as declarative data rather than imperative code. This separation lets non-developers compose agent teams conversationally while developers retain full programmatic control. Swarms are not static: every swarm can observe its own capability gaps and grow new skills through a conversational, human-approved authoring flow.

**Status:** pre-v1.0. Design phase. See [`design/`](./design/) for the authoritative architecture (v0.6).

## Monorepo layout

```
swarmkit/
├── design/              # Architecture docs (v0.6 is current)
├── packages/
│   ├── runtime/         # swarmkit-runtime — Python CLI + HTTP server, LangGraph compiler, AGT wiring
│   ├── schema/          # swarmkit-schema — canonical JSON Schemas + Python & TypeScript validators
│   │   ├── schemas/     #   canonical JSON Schema files (source of truth)
│   │   ├── python/      #   Python validator package
│   │   └── typescript/  #   TypeScript validator package
│   └── ui/              # swarmkit-ui — Next.js composer, authoring interface, runtime dashboard
├── reference/           # v1.0 reference artifacts (topologies, archetypes, skills)
├── docs/                # User-facing documentation
├── scripts/             # Dev scripts (design extraction, etc.)
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

## Packages

| Package | Language | Distribution | Status |
| --- | --- | --- | --- |
| [`swarmkit-runtime`](./packages/runtime) | Python 3.11+ | PyPI + Docker | scaffolded |
| [`swarmkit-schema`](./packages/schema) | Python + TypeScript | PyPI + npm | scaffolded |
| [`swarmkit-ui`](./packages/ui) | TypeScript / Next.js | npm + hosted | scaffolded |

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
