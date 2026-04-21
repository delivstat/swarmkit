---
title: TypeScript type codegen
description: Generate TypeScript interfaces from the canonical JSON Schemas via quicktype. Mirror of pydantic codegen.
tags: [schema, codegen, typescript, m0]
status: implemented
---

# TypeScript type codegen

**Scope:** `packages/schema/typescript/src/types/`
**Design reference:** `docs/notes/schema-change-discipline.md`, `design/details/pydantic-codegen.md` (parallel pattern for Python).
**Status:** in review

## Goal

Generate TypeScript type interfaces from the five canonical JSON Schemas so UI code and any downstream TS consumer gets typed access to SwarmKit artifacts. Mirror of the pydantic codegen — same source of truth, same drift-protection story, same shape-vs-full-validation split.

## Non-goals

- **Replacing `validate()` as the authoritative validator.** `@swarmkit/schema.validate(kind, data)` remains the single entry point that fully enforces the schema via Ajv. TS types are compile-time-only; runtime validation still belongs to Ajv.
- **Closing the `allOf`/`if-then` gap.** Decided on the pydantic PR (see `design/details/pydantic-codegen.md` — section "Decision — leave the gap open") and applies identically here. TS types cover shape; `validate()` covers everything.
- **Runtime behaviour.** TS types are erased at compile time. They provide IDE autocompletion + compile-time sanity; they do nothing at runtime.

## Tool choice — quicktype

`json-schema-to-typescript` was the first candidate but it trips on recursive `$defs` — our topology schema has `agent` → `children` → `child_agent` → `agent`, and skill schema has `field` → `items: field` / `properties: field` loops. The library throws `Refs should have been resolved by the resolver` on both.

`quicktype-core` handles JSON Schema 2020-12 including recursive `$defs`. One quirk: for recursive leaf types it names them "`<root-name>` minus last character" (e.g. `SwarmKitTopolog`, `SwarmKitSkil`). The generated code is structurally correct but the names are awkward.

We post-process the output to rename these awkward types to meaningful domain names:

- `SwarmKitTopolog` → `ChildAgent` (topology.ts)
- `SwarmKitSkil` → `FieldSpec` (skill.ts)

The rename is a word-boundary regex replace, applied per artifact in the codegen script.

## Generated surface

```
packages/schema/typescript/src/types/
├── index.ts              # re-exports the 5 root types
├── topology.ts           # SwarmKitTopology + ChildAgent + nested interfaces
├── skill.ts              # SwarmKitSkill + FieldSpec + nested interfaces
├── archetype.ts          # SwarmKitArchetype + nested interfaces
├── workspace.ts          # SwarmKitWorkspace + nested interfaces
└── trigger.ts            # SwarmKitTrigger + nested interfaces
```

Public API — `src/index.ts` re-exports the five root types alongside `validate` / `getSchema`:

```ts
import {
  validate,
  type SwarmKitTopology,
  type SwarmKitSkill,
  type SwarmKitArchetype,
  type SwarmKitWorkspace,
  type SwarmKitTrigger,
} from "@swarmkit/schema";

const raw = parseYaml(text);
const result = validate("topology", raw);           // authoritative
if (result.valid) {
  const topology = raw as unknown as SwarmKitTopology;  // typed access
  topology.agents.root.id;                               // IDE-autocompleted
}
```

## Drift protection

Two justfile targets mirror the Python pattern:

- `just schema-codegen-ts` — regenerate types.
- `just schema-codegen-ts-check` — regenerate, fail if working tree is dirty.
- `just schema-codegen` — runs both Python and TS regen.
- `just schema-codegen-check` — runs both drift checks.

The existing `schema codegen drift` CI job is extended to run both regens and fail on either drift. One job, two checks.

## Biome configuration

`packages/schema/typescript/biome.json` excludes `src/types/**` from lint + format so biome doesn't complain about `any` or style choices the generator makes. The types are consumed through `index.ts` and never imported by humans inside this package.

## Test plan

`packages/schema/typescript/tests/types.test.ts` covers:

- **Type-level exports:** each of the five root types is exported and resolvable. Failure mode: `tsc --noEmit` fails to compile the test file.
- **Fixture assignability:** each valid fixture validates via Ajv, then narrows to its root type, then accesses typed fields (`.metadata.id`, `.agents.root.role`, etc.). Failure mode: schema's shape diverges from what `tsc` thinks the type is.

The existing `index.test.ts` keeps the broader fixture validation coverage (76 tests). Types tests add 32 more cases.

## Demo plan

`just demo-codegen` now runs both languages:

```
$ just demo-codegen
loading every valid fixture through its generated pydantic model:
  ✓ topology: 5 fixtures loaded OK
  ... (5 kinds)

round-trip — topology/from-design-doc.yaml → SwarmKitTopology → JSON:
  topology.metadata.name  = 'code-review-excerpt'

loading every valid fixture through validate() + typed narrow:
  ✓ topology: 5 fixtures loaded OK
  ... (5 kinds)

typed TS round-trip — topology/from-design-doc.yaml → SwarmKitTopology
  topology.metadata.name  = "code-review-excerpt"
```

## Open questions / follow-ups

- **Zod-style runtime-validating types.** An alternative path is `zod-from-json-schema` or `typebox`: the TS side gets runtime-validating types that double as compile-time types, closing the shape/full gap on the TS side. Not yet — Ajv via `validate()` already does the runtime validation; adding zod would duplicate concerns. Revisit if the UI's editor integration needs finer-grained error paths.
- **The awkward rename list** (`SwarmKitTopolog → ChildAgent`, `SwarmKitSkil → FieldSpec`) is hand-maintained per artifact. If we add another recursive $def, the codegen will emit another awkward name and needs another rename entry. Low-frequency; catching regressions is easier than preventing them.
- **Publish-time bundling.** Currently the `schemas/` directory must be copied into the TS package for npm publish (see Milestone 10). The `copy-schemas.mjs` script already exists; wire it into the publish pipeline when that milestone arrives.
