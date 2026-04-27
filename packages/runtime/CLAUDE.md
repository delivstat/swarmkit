# CLAUDE.md — packages/runtime

## Package identity

`swael-runtime` — the Python component of the three-package system. Loads topology files, compiles them into LangGraph graphs, enforces governance through the `GovernanceProvider` abstraction, and exposes the `swael` CLI + FastAPI server.

The design doc is the authoritative source for architectural decisions — see `design/Swael-Design-v0.6.md`. §9, §14, §16, §18 are the most relevant sections to this package.

## Module map

| Module | Responsibility | Design ref |
| --- | --- | --- |
| `cli/` | Typer-based CLI, authoring entry points | §14.2 |
| `topology/` | Load, validate, resolve topology YAML/JSON | §10 |
| `skills/` | Skill registry + category semantics (capability, decision, coordination, persistence) | §6 |
| `archetypes/` | Archetype registry and instantiation | §13 |
| `governance/` | `GovernanceProvider` interface + `AGTGovernanceProvider` | §8.5, §16.5 |
| `langgraph_compiler/` | Topology → `StateGraph` dynamic construction | §14.3 |
| `mcp/` | MCP client + sandboxed server lifecycle | §18 |
| `audit/` | Append-only audit log, skill gap log surfacing | §14.5, §16.4 |

## Non-negotiable invariants

These come from the design's architectural principles and separation-of-powers model. Do not relax without explicit approval.

1. **Topology is data.** Never generate or inline topology as Python code inside this package. The runtime reads YAML/JSON and interprets.
2. **All governance goes through `GovernanceProvider`.** No direct AGT imports outside `governance/`. If code needs policy evaluation, identity, or audit, it goes through the interface.
3. **Audit is append-only from agent perspective.** No code path should expose `update` or `delete` on audit entries to executive-layer callers.
4. **Pillar boundaries are enforced at the module level** (design §8.4). Executive code invokes skills via middleware that routes through the policy engine — there is no bypass.
5. **Skills are the only extension primitive.** When adding a capability, add a skill category or skill definition — do not introduce parallel extension mechanisms.
6. **Eject must stay intact.** Every feature you add to the runtime needs an ejection story; if it cannot be expressed in generated LangGraph code, reconsider the design.

## Style

- Python 3.11+, strict typing (`mypy --strict`).
- Prefer `pydantic` models for all schema-shaped data; use `dataclass` only for internal value objects.
- Async-first where I/O is involved (`anyio` / `httpx` / `fastapi`).
- Error taxonomy: custom exceptions live in each module's `_errors.py`; no bare `raise Exception`.

## Testing

- `pytest` + `pytest-asyncio`. Tests live under `tests/`.
- Integration tests that touch real MCP servers or AGT gate on env vars; unit tests mock at the `GovernanceProvider` seam.
- Every reference topology ships a smoke test that loads + compiles it without executing.

## Commands

```bash
uv run pytest packages/runtime/tests             # tests
uv run ruff check packages/runtime               # lint
uv run mypy packages/runtime                     # typecheck
uv run swael --help                           # CLI entry
```

Or via the root justfile: `just test-py`, `just lint-py`, `just typecheck-py`.
