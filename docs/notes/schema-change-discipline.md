# Schema-change discipline

When you change a schema, you touch more than one file. Miss any of them and the Python validator, TypeScript validator, and generated types silently diverge. This note is the checklist.

## The layered surface

```
packages/schema/
├── schemas/*.schema.json         ← SOURCE OF TRUTH (one per artifact type)
├── tests/fixtures/*/             ← SHARED by both languages' tests
├── python/
│   └── src/swael_schema/
│       └── models/*.py           ← CODEGEN from schemas (M0 Task #14; not yet landed)
└── typescript/
    └── src/types/*.ts            ← CODEGEN from schemas (M0 Task #15; not yet landed)
```

| Layer | Hand-authored? | Who consumes it? |
|---|---|---|
| `schemas/*.schema.json` | **Yes — the one source of truth.** | Both validators, both test suites, the runtime, the UI. |
| `tests/fixtures/*/` | Yes. | Both Python and TS tests read the same files. |
| `python/src/swael_schema/models/` | **No — generated.** Regenerate with `just schema-codegen-py`. | Runtime code as typed pydantic models. |
| `typescript/src/types/` | **No — generated.** Regenerate with `just schema-codegen-ts`. | UI / TS consumers as TypeScript interfaces. |
| `python/src/swael_schema/__init__.py` | Yes. | Thin wrapper; loads + validates against the JSON Schemas. Does not redefine shape. |
| `typescript/src/index.ts` | Yes. | Thin wrapper; same rule. |

## The rule

**There is only one place where schema shape is defined: the `.schema.json` files.** Validators and codegen targets consume that shape. If you find yourself editing a pydantic model or a TS type directly, stop — regenerate instead.

## Checklist — any schema change

Every time you change anything under `packages/schema/schemas/`:

1. **Edit the `.schema.json` file.** That's the change.
2. **Add or update fixtures** under `packages/schema/tests/fixtures/<artifact>/` — at least one valid fixture exercising the new surface, and where relevant one invalid fixture that the new rule would reject.
3. **Do not touch `validate` or `getSchema` wrappers unless the public API shape changes.** They re-read the schema on every call or via import-time load — the new shape is picked up automatically.
4. **Regenerate pydantic models AND TS types.** `just schema-codegen` runs both regenerators. Commit the regenerated output in the same PR as the schema change. CI's `schema codegen drift` job runs both regens and fails on either drift.
5. **Run the matching demo target:**
   - `just demo-topology-schema` for topology changes
   - `just demo-schema` for a combined run across every artifact
   - `just demo-codegen` to see a typed object loaded through the generated pydantic models and the generated TS types
6. **Design note.** If the change is non-trivial, add or update `design/details/<artifact>-schema-v1.md`. If the change is a cosmetic fix (typo, description rewording), a PR without a design note is fine.

### Shape vs full validation

Generated pydantic models and TS types both cover shape (required fields, types, enums, patterns) but do not translate `allOf` / `if-then` conditional rules. Full validation happens through `swael_schema.validate()` in Python and `@swael/schema.validate()` in TypeScript — both use JSON Schema directly. Runtime code calls `validate()` first, then narrows to the typed interface. See `design/details/pydantic-codegen.md` and `design/details/ts-codegen.md` for the enumerated list of rules and the explicit decision to leave this gap open in favour of validation-UX investment (task #23).

## Why this matters

Two languages validating against one schema is the contract we make with the community: a topology file is valid regardless of which language's tooling reads it. Drift between Python and TS would break that contract silently — a file that passes `swael validate` in Python might fail in the UI's live validation.

Similarly, generated models must never drift from the schema. A hand-edited pydantic model that adds a field not in the JSON Schema would let runtime code pass through artifacts that the validator would reject — the framework's type safety would be a lie.

## See also

- `packages/schema/CLAUDE.md` — per-package invariants.
- `design/details/topology-schema-v1.md` (and the four siblings for skill / archetype / workspace / trigger) — where the decisions behind the current schema shape are recorded.
- `design/IMPLEMENTATION-PLAN.md` Milestone 0 — lists the codegen PRs (#14 pydantic, #15 TS types).
