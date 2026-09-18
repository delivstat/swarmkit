# Memory by default

**Status:** design · **Scope:** schema + runtime + portal + docs · **Follows:** `governed-memory.md`, `memory-and-decision-skills` guide, Level 9

## The gap

Memory is the feature people expect an assistant to have, and today it is the feature that takes
the most wiring. Level 9 has a user bind `memory-reader` and `memory-writer` by hand under
`governance.decision_skills` (with the right trigger, scope, `required: false` and config), copy
two reference skill files into `skills/` to make governed memory exist at all, and only then grant
`governed-memory` to an agent. Four steps for "remember what I told you", three of which are the
same in every workspace.

The runtime already knows all of it: `memory-reader` and `memory-writer` are built-in decision
skill ids, and `governed-memory` / `memory-reconcile` are reference skills the runtime ships. What
is missing is the default.

## Goal

A workspace has memory unless it says otherwise. `swarmkit init`'s output remembers; a workspace
that never mentions memory remembers; one `enabled: false` turns off everything automatic —
what a workspace wires explicitly (a copied `governed-memory` skill, say) still works.

## Non-goals

- Granting `governed-memory` (the *write* skill) to agents automatically. A curated store an
  agent can write to is a grant a person makes, per agent, as today (`skills_additional`). Reading
  curated facts needs no grant and is on by default.
- Changing what `memory-reader`/`memory-writer` do, or the store's schema.
- A portal form for the block. `memory` sits beside `governance` in the workspace file and follows
  its rule: not form-editable (`_workspace_config.EDITABLE`), visible read-only where it acts —
  the portal's Memory page shows the effective configuration.

## Shape

### Workspace schema

```yaml
memory:                      # optional; absent ⇒ enabled with defaults
  enabled: true
  reader:                    # config for the auto-bound memory-reader
    max_results: 5
    similarity_threshold: 0.15
    search_scope: all        # user | all | both
  writer:                    # config for the auto-bound memory-writer
    min_output_length: 100
```

`memory-reader`/`memory-writer` **explicitly** bound under `governance.decision_skills` keep
winning — their trigger, scope, `required` and `config` are used as written, and the auto-binding
for that id is skipped. `enabled: false` with an explicit binding is a resolution error
(`memory.disabled-but-bound`) rather than a silent choice between the two.

### Runtime

`memory/_defaults.py`:

- `apply_memory_defaults(raw_workspace: dict) -> dict` — when enabled, appends the missing
  reader/writer bindings (reader `pre_input`, writer `post_output`, scope `*`, `required: false`,
  config from the block merged over the defaults) to `governance.decision_skills`. Called by the
  resolver before the workspace model is built, so everything downstream — `merge_decision_skills`,
  `swarmkit validate --require`, the portal — sees one list.
- `bundled_memory_skills() -> list[DiscoveredArtifact]` — the `governed-memory` and
  `memory-reconcile` skill artifacts from `memory/skills/*.yaml` (byte-identical copies of the
  reference files; a test enforces it). Injected by the resolver when enabled and the workspace
  defines no skill with that id, so the governed store is built and the reconciler wired. A
  workspace copy still overrides.
- The resolver reports the effective block on `ResolvedWorkspace.memory` so the CLI and server can
  show it; `GET /memory/status` (existing) gains `config`.

### Portal

The Memory page reads `config` from the status and shows "Memory: on — reader top 5 ≥ 0.15, writer
≥ 100 chars" or "Memory: off (`memory.enabled: false`)". Nothing editable.

### Cost, stated

`memory-writer` is one model call per run whose output clears `min_output_length`. That is the
price of remembering and it is now paid by default; the docs say so next to the opt-out, and
`min_output_length` is the knob before `enabled`.

## Test plan

- schema: valid fixture with the block, invalid fixture (`search_scope: everything`); codegen
  drift job.
- runtime: a workspace with no `memory` block resolves with both bindings and both skills; an
  explicit reader binding is kept verbatim and only the writer is added; `enabled: false` yields
  neither binding and no bundled skills (an explicitly copied skill still builds the store); `enabled: false` + explicit binding → resolution error;
  the bundled skill files equal `reference/skills/*`; a workspace copy of `governed-memory` wins;
  `swarmkit run` on the mock provider with default memory writes nothing on a short answer and
  writes on a long one (existing writer tests, now under defaults).
- portal: Memory page renders the effective configuration.

## Demo plan

Level 9 rewritten from a real run: a fresh `swarmkit init` workspace, no memory wiring, "two things
to remember about me…", a second run that recalls them, the Memory page showing the config, then
`enabled: false` and the same second run forgetting. Levels 1–8 example workspaces run under the
default and their transcripts are re-checked where memory now speaks.
