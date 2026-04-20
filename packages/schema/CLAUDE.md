# CLAUDE.md — packages/schema

## Package identity

Language-agnostic JSON Schemas for SwarmKit's five core artifact types, plus Python and TypeScript validators. Published as `swarmkit-schema` (PyPI) and `@swarmkit/schema` (npm). Versioned independently of the runtime and UI so the schema can evolve without forcing a runtime release.

## The dual-surface rule

**`schemas/*.schema.json` is the source of truth.** Both the Python and TypeScript packages consume those files — neither may redefine shape. Validation behaviour, error formatting, and framework integration (pydantic, Zod, etc.) are language-specific and live in the respective package.

When the JSON Schema changes:
1. Update the `.schema.json` file.
2. Re-run codegen in both language packages (pydantic models, TS types).
3. Add or update tests in both packages.
4. Bump the schema version per the `apiVersion` rule (design §10.1).

## Schema versioning

From design §9.1: "Versioned independently." The `apiVersion: swarmkit/v1` field in every artifact is the contract. Breaking changes require a new major (`v2`) and a migration path documented in `design/`.

## Non-negotiable invariants

1. **No runtime dependency on Python or TypeScript validators from the schemas themselves.** A consumer in any language must be able to validate artifacts using only the `.schema.json` files.
2. **Skills are referenced by ID, not inline.** The topology schema must not permit inline skill definitions — they live in their own files (design §6.1, §10.2).
3. **Provenance is required on skill and archetype definitions** (design §6.4). The schema enforces this.
4. **IAM scopes are structured, not free-text.** Scope values validate against an enum or pattern — the separation-of-powers model depends on scope integrity (design §8.7).

## Commands

```bash
uv run pytest packages/schema/python/tests          # Python tests
pnpm --filter @swarmkit/schema test                  # TS tests
```
