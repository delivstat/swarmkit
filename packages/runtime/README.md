# swarmkit-runtime

Python runtime for SwarmKit. Interprets topology files, compiles them into LangGraph `StateGraph`s, enforces governance via the `GovernanceProvider` abstraction (AGT-backed in v1.0), and exposes the `swarmkit` CLI plus a persistent HTTP server.

## Layout

```
src/swarmkit_runtime/
├── cli/                 # Typer-based CLI: init, author, run, serve, eject
├── topology/            # Topology loader, validator, resolver (archetype + skill refs)
├── skills/              # Skill registry, category-specific semantics, composition
├── archetypes/          # Archetype registry and instantiation
├── governance/          # GovernanceProvider interface + AGTGovernanceProvider impl
├── langgraph_compiler/  # Topology → StateGraph compilation (design §14.3)
├── mcp/                 # MCP client, server lifecycle, sandbox supervision
└── audit/               # Append-only audit log adapters, skill gap log surfacing
```

## Design references

- §7 Architectural Principles — `topology as data`, `eject, never lock-in`
- §8 Separation of Powers — `governance/` module is the SwarmKit side; AGT is the implementation
- §9 System Architecture — this package is component #1 of 3
- §14 Runtime Architecture — three execution modes (one-shot, persistent, scheduled)

## Entry points (design §14.2)

| Command | What it does |
| --- | --- |
| `swarmkit init` | Launch Workspace Authoring Swarm in terminal chat mode |
| `swarmkit author topology [name]` | Launch Topology Authoring variant |
| `swarmkit author skill [name]` | Launch Skill Authoring Swarm |
| `swarmkit author archetype [name]` | Launch Archetype Authoring variant |
| `swarmkit run topology.yaml` | One-shot execution |
| `swarmkit serve workspace/` | Persistent / scheduled mode |
| `swarmkit eject topology.yaml` | Export LangGraph code |

## Development

```bash
uv sync --package swarmkit-runtime
uv run pytest packages/runtime/tests
uv run swarmkit --help
```
