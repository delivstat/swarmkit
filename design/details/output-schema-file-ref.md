# `output_schema` may name a file

Status: implemented (runtime 1.148.0, swarmkit-schema 1.25.0).

## Why

`output_schema` is inline-only today. Three costs, in increasing order of how much they hurt:

1. **A JSON Schema inline in YAML is hard to read** at any real size, and it sits in the middle of
   an agent block, pushing the rest of the agent's configuration off the screen.
2. **It cannot be shared.** Two agents that must agree on a shape have to duplicate it, and the
   duplicates drift silently. That is the same failure this repo has already paid for twice — a
   thing that exists in two places with nothing keeping them equal.
3. **It is never validated as a schema.** Nothing checks the inline object is a well-formed JSON
   Schema until an agent's output is measured against it — so a typo in a `required` list surfaces
   at run time, mid-pipeline, as a conformance failure that blames the agent.

## Shape

The declaration is **already** polymorphic — `oneOf: [object, null]`. This adds a third branch:

```yaml
output_schema: { type: object, required: [screens] }   # inline, unchanged
output_schema: ./schemas/design-spec.json              # a file
output_schema: null                                    # opt out, unchanged
```

```json
"output_schema": {
  "oneOf": [
    { "type": "object", "additionalProperties": true, "description": "JSON Schema, inline." },
    { "type": "string", "minLength": 1, "description": "Path to a JSON Schema file, relative to the topology that declares it." },
    { "type": "null", "description": "Opt out of output schema for this agent." }
  ]
}
```

### One key, not two

A separate `output_schema_ref` would make it possible to write both, and then something has to
decide which wins. Whatever that rule is, the loser is **silently ignored** — so an author edits the
referenced file, sees no effect, and gets no message. That is the exact failure shape this codebase
keeps paying for, and there is no case where writing both is meaningful: precedence here is wholesale
replacement, not merging.

One type-discriminated key makes the ambiguity **unrepresentable** rather than resolved. Nothing to
decide, nothing to document, nothing to get wrong.

### Precedence is unchanged

`_merge_output_schema` already resolves archetype default vs agent override, and agent-level already
wins (with explicit `null` meaning opt-out). A file ref slots into that rule at either level, in
either direction:

| archetype | agent | result |
| --- | --- | --- |
| `./schemas/base.json` | *(absent)* | the archetype's file |
| `./schemas/base.json` | `{ … }` inline | the agent's inline schema |
| `{ … }` inline | `./schemas/strict.json` | the agent's file |
| anything | `null` | opt out |

This is the override the request asked for, and it already works — the only change is that either
side may now be a path.

## Resolution

**Relative to the topology file that declares it**, not to the workspace root. A schema lives beside
the topology that uses it, or in a shared directory reached with `../`. `DiscoveredArtifact` already
carries `path`, so nothing new has to be threaded to know where the declaring file is.

Two guards:

- **The resolved path must stay inside the workspace.** Same rule the docs-reader already enforces
  (`_server.py` `is_relative_to`), for the same reason: an artifact should not be able to read
  `../../../etc`. This needs the workspace root, which means one new parameter on
  `build_topology_registry` and its single call site.
- **No remote URLs.** A schema fetched at resolve time makes a workspace's meaning depend on the
  network, and on whatever the other end serves that day. If a shared schema is wanted across
  repositories, that is a package problem, not a loader problem.

## Validation at load time

The referenced file must parse as JSON (or YAML — the repo's artifacts are YAML, and a schema
author will reasonably reach for it) **and** be a well-formed JSON Schema. All three failures are
resolution errors with the declaring file and the offending path named:

- file not found
- file does not parse
- file parses but is not a valid JSON Schema (checked with the same
  `jsonschema` draft validator the runtime already depends on)

**Inline schemas get the same check**, which they do not have today. That is arguably the larger win:
a malformed inline schema currently fails at run time and reads like the agent's fault.

## Normalisation

The loader resolves a path to its parsed object and stores that. `ResolvedAgent.output_schema` stays
`Mapping[str, Any] | None`, so **nothing downstream changes** — not `_output_gov`, not
`get_effective_output_schema`, not the harness enforcement added in 1.143.0. A consumer cannot tell
which form was written, which is the point: the file is a authoring convenience, not a new runtime
concept.

One consequence worth stating: the schema is read **once, at resolve time**. Editing the file does
not affect a run already in flight, and `swarmkit serve` picks it up on reload like any other
artifact change. That is the same rule every other artifact follows.

## Non-goals

- **Resolving `$ref` inside the schema file.** A JSON Schema may use `$ref` internally; that is the
  validator's business and works already. This proposal is about where the schema *document* lives.
- **A schema registry, or versioned schema packages.** If that becomes wanted, it belongs beside the
  skill/archetype packaging story, not bolted onto one field.
- **Changing the worker platform default.** `get_effective_output_schema` still falls back to it for
  `role: worker` on the model path, and still does not on the harness path (see
  `harness-output-schema.md`).

## Test plan

- a string resolves to the file's parsed content; the resolved agent is byte-identical to the
  equivalent inline declaration (the normalisation property, stated as an equality)
- relative paths resolve against the declaring topology, including `../shared/`
- a path escaping the workspace is a resolution error
- missing file / unparseable file / not-a-valid-JSON-Schema are three distinct resolution errors,
  each naming the declaring artifact
- an inline schema that is not a valid JSON Schema is now also a resolution error (new behaviour;
  needs a note in the changelog since a previously-loadable workspace may now fail to load)
- archetype/agent precedence across all four combinations in the table above
- `null` still opts out from either level
- two agents referencing one file get the same schema object

Schema work follows `docs/notes/schema-change-discipline.md`: source, bundled copy, both codegen
targets, a valid fixture using the string form, and an invalid one.

## Resolved: the inline check errors immediately

A malformed schema was never doing what its author believed, and a warning ignored for a release is
a slower way to reach the same place. It is a resolution error from 1.148.0.

One existing fixture asserted the opposite of part of this change —
`archetype-invalid/output-schema-bad-type.yaml` used a *string* as its example of an invalid
`output_schema`, which is now valid. It was updated to use a number, and says so, because the
fixture is where that invariant was written down.
