---
status: accepted
---

# SchemaForm — composite schema constructs (oneOf / allOf / additionalProperties)

The designer's auto-generated forms (`packages/ui`, `SchemaForm`) render a field per JSON-Schema
property. They handle scalars, enums, arrays, and fixed-property objects well, but fall down on three
constructs the canonical schemas use heavily — so real artifacts show raw-JSON blobs or empty rows:

| Construct | Today | Where it bites |
| --- | --- | --- |
| `oneOf` / `anyOf` | JSON blob (`fieldKind` → `"json"`) | skill `implementation` (mcp_tool / llm_prompt / composed) |
| `allOf` | JSON blob | topology `agents → root` (the `agent` def is `allOf`) |
| `object` with only `additionalProperties` | renders **nothing** (`objectFields` → `[]`) | executor `config`, model `options`, skill `input`/`output`, `knowledge_bases`/`review_queues` rows, governance decision-skill `config` |

These are one fix each, and together they clear the whole reported list. The form stays
**schema-driven** — no hardcoded per-artifact knowledge (invariant #2); we teach the generic walker
three more shapes.

## 1. `oneOf` / `anyOf` — a variant editor

A discriminated union: pick a variant, edit its fields.

- **Discriminator.** Prefer a `type` (or `kind`) property that is a `const` in each variant (the
  convention the schemas use — implementation's `type: mcp_tool|llm_prompt|composed`). Fall back to
  the variant `title`, else its index.
- **Render.** A dropdown of the variants (labelled by their discriminator const / title). Selecting
  one renders that variant's object fields; the discriminator value is written into the object so the
  document round-trips valid. Switching variants resets to that variant's shape but keeps any
  same-named fields' values.
- **Current value → active variant.** Match the value's discriminator to a variant; else the first.

## 2. `allOf` — merge, then render as one object

`allOf` means "satisfy all of these" — for form purposes, the union of their fields.

- In the schema walker, when a (resolved) node has `allOf`, **merge** each member (each resolved) into
  a synthetic object: concatenate `properties`, union `required`, and carry `additionalProperties` if
  any member sets it. A member with its own `type: object` + properties contributes them; a member
  that is itself `oneOf` is left for the variant editor (rare; degrade to JSON if mixed).
- Then classify + render the merged node as a normal object. This makes `agents.root` (base agent
  fields + `children`) a proper nested form.

## 3. `additionalProperties` — a key/value map editor

An `object` with `additionalProperties` (schema or `true`) and **no or few fixed `properties`** is a
free-form map (env vars, provider `options`, opaque `config`, a knowledge-base entry keyed by id).

- **Render.** The fixed properties (if any) as normal fields, then a **map editor**: each entry is a
  `key` text input + a value field *driven by the `additionalProperties` schema* (so a
  `Record<string, string>` gets string inputs, a richer value schema gets its sub-form); an `×` per
  row and an `+ add entry`. Renaming a key preserves the value.
- **When.** A node is a "map" when it has `additionalProperties` truthy and the value isn't better
  served as fixed fields. Heuristic: `additionalProperties` present AND (`properties` empty OR the
  data has keys outside `properties`). Fixed-property objects with `additionalProperties: false` are
  unchanged.

## Fallback

Truly-open schemas (`{}`, or `additionalProperties: true` with an unknown value shape) still get the
JSON editor — but initialised to `{}`/`[]` per any `type`, not the confusing bare `null`.

## Non-goals

- Not full JSON-Schema (no `if/then/else`, `dependentSchemas`, `patternProperties` beyond treating
  them as a map, `$ref` across files). The canonical schemas don't use those in author-facing fields.
- Not changing the schemas — this is renderer-only. (`x-swarmkit-ref` pickers, already shipped, are
  untouched.)

## Test plan

- **Pure (`lib/schema-form.ts`):** `allOf` merge (properties + required union); a "map" detector
  (additionalProperties, empty vs present properties); `oneOf` variant extraction + discriminator
  resolution; `fieldKind` no longer returns `"json"` for these three.
- **Component:** a oneOf field switches variants and writes the discriminator; a map field adds /
  renames / removes entries; an allOf object renders merged fields — each asserted via the real
  skill / archetype / topology schemas (import the bundled JSON, render, assert the fields appear —
  no more blob).

## Demo / acceptance

In the composer **form** view against a real workspace: skill `implementation` shows a type dropdown
+ that variant's fields (not JSON); archetype `executor.config` / model `options` show an editable
key/value map; topology `agents.root` shows nested agent fields; a governance decision-skill's
`config` and a `knowledge_bases` row are editable. No author-facing field renders as a raw-JSON blob
or an empty row.
