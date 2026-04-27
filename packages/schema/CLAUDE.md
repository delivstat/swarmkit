# CLAUDE.md — packages/schema

## Package identity

Language-agnostic JSON Schemas for Swael's five core artifact types, plus Python and TypeScript validators. Published as `swael-schema` (PyPI) and `@swael/schema` (npm). Versioned independently of the runtime and UI so the schema can evolve without forcing a runtime release.

## The dual-surface rule

**`schemas/*.schema.json` is the source of truth.** Both the Python and TypeScript packages consume those files — neither may redefine shape. Validation behaviour, error formatting, and framework integration (pydantic, Zod, etc.) are language-specific and live in the respective package.

**If you're about to touch a schema, read [`docs/notes/schema-change-discipline.md`](../../docs/notes/schema-change-discipline.md) first.** It's the authoritative checklist — which files change, which get regenerated, what tests / fixtures / demos must land alongside. Keep it in sync when the schema surface grows.

## Schema versioning

From design §9.1: "Versioned independently." The `apiVersion: swael/v1` field in every artifact is the contract. Breaking changes require a new major (`v2`) and a migration path documented in `design/`.

## Non-negotiable invariants

1. **No runtime dependency on Python or TypeScript validators from the schemas themselves.** A consumer in any language must be able to validate artifacts using only the `.schema.json` files.
2. **Skills are referenced by ID, not inline.** The topology schema must not permit inline skill definitions — they live in their own files (design §6.1, §10.2).
3. **Provenance is required on skill and archetype definitions** (design §6.4). The schema enforces this.
4. **IAM scopes are structured, not free-text.** Scope values validate against an enum or pattern — the separation-of-powers model depends on scope integrity (design §8.7).

## Commands

```bash
uv run pytest packages/schema/python/tests          # Python tests
pnpm --filter @swael/schema test                  # TS tests
```
