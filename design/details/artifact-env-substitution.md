# Artifact env-variable substitution (runtime)

**Scope:** runtime (artifact load path)
**Status:** implemented — runtime 1.97.0 (PR #605)
**Related:** [`workspace-env-config.md`](workspace-env-config.md) (the two-file
workspace property map this builds on)

## Problem

Env-variable substitution in SwarmKit is piecemeal: only **MCP server config** expands `${VAR}`
(`mcp/_client._expand_var` / `_expand_env_value`). Everything else in an artifact — an archetype's
`model.name`, a topology's prompt, a trigger's target — is literal. So a reusable library (e.g. the
SDLC archetype library) can't say "use whatever model the deployment configured" without per-app
plumbing. Minder works around this with a bespoke `${model.<tier>}` context that is Minder-specific.

## Goal

Make `${VAR}` substitution a **default runtime feature across all artifact YAML** — topology, skill,
archetype, workspace, trigger — resolved once at load time from the process environment, so any
string value in any artifact can reference the environment. Reusable artifacts become configurable
without code.

## Syntax

- **`${VAR}`** — replaced by the value of `VAR`.
- **`${VAR:-default}`** — `VAR` if set and non-empty, else `default` (bash-style). Defaults matter so
  a library ships working out-of-box: `name: ${SDLC_REASONING_MODEL:-moonshotai/kimi-k2.5}`.
- **`$${...}`** — a literal `${...}` (escape), for the rare artifact that must contain the sequence.

## Resolution order

Per `${NAME}`, the resolver tries, in order:

1. **Workspace property map** — dotted paths from `workspace.env.yaml` /
   `workspace.env.{SWARMKIT_ENV}.yaml` (see
   [`workspace-env-config.md`](workspace-env-config.md)). Empty when there is no
   env file, so the remaining steps still apply.
2. **OS environment** — `os.environ[NAME]`.
3. **Inline default** — the text after `:-`, when written `${NAME:-default}`.
4. **Left literal** — an unresolved ref with no default is emitted unchanged.

The property map winning over the OS environment lets a workspace pin a value
while an env-only deployment (no env file) falls through to steps 2–4.

## Rules

- **Unresolved-with-no-default is left literal, not fail-loud.** A missing
  `${VAR}` with no `:-default` is emitted unchanged (`${VAR}`) rather than
  raising or expanding to empty. This is the backward-compatibility guarantee: an
  artifact that already contains a `${...}` sequence never regresses when this
  default turns on. (The design first proposed fail-loud; the shipped behaviour is
  leave-literal, chosen so enabling substitution across *all* artifacts could not
  break any existing workspace. A future `swarmkit validate` warning is the place
  to surface unresolved refs — see open questions in
  [`workspace-env-config.md`](workspace-env-config.md).)
- **Load-time, once.** Substitution runs when the workspace is resolved
  (`_apply_env_interpolation` in `resolver/__init__.py`), before schema
  validation, so validators + the runtime see resolved values.
- **Whole tree.** String scalars anywhere in the parsed artifact tree are
  expanded; dicts and lists are traversed recursively.
- **Strings only.** Numbers/bools are untouched; a `${VAR}` only expands inside a
  string value.
- **No recursion.** The result of a substitution is not itself re-scanned (avoids
  surprise + loops).

## Where it hooks

A single load-time pass, post-parse (chosen over raw-text expansion so escaping +
non-string values are unambiguous). `resolve_workspace` calls
`_apply_env_interpolation` after discovery and before schema validation
(`packages/runtime/src/swarmkit_runtime/resolver/__init__.py`); it walks each
artifact's `raw` dict tree and expands string scalars in place. The engine lives in
`resolver/_env_config.py`:

- `load_env_config(workspace_root)` builds the flat dotted property map (empty if
  no env file), resolving `${ENV_VAR}` in property values.
- `interpolate_dict` / `interpolate_value` apply the four-step resolution to an
  artifact tree; `$${...}` escapes to a literal `${...}`.

## Non-goals

- Not a templating language (no conditionals, loops, or arithmetic) — just variable substitution
  with defaults.
- Not secret management — values still come from the environment; this only references them.
- Not the Minder `${model.<tier>}` context — that indirection is superseded by plain `${ENV_VAR}`
  refs (Minder can migrate its archetypes to env refs + drop the bespoke context).

## Test plan

Covered by `packages/runtime/tests/test_env_config.py`:

- **Basic:** `${VAR}` expands; `${VAR:-default}` uses the default when unset and the value when set.
- **Order:** property map wins over env; env wins over default.
- **Leave-literal:** an undefined `${VAR}` with no default is emitted unchanged (not raised, not
  emptied), including via `_apply_env_interpolation` with **no** `workspace.env.yaml`.
- **Escape:** `$${X}` yields literal `${X}`.
- **Whole tree:** substitution recurses through nested dicts and lists.
- **Property map load:** default vs named env file, `SWARMKIT_ENV` selection, fallback, absent/invalid
  YAML.

## Demo

`packages/runtime/demos/env_substitution.py` (`uv run python …`) runs the real resolver — no model
calls — and prints each mode: default-when-unset, env-overrides-default, property-map-wins, `$${VAR}`
escape, and unresolved-left-literal.

## Consumers

- **SDLC archetype library** (`examples/sdlc-pipeline/`) — `${SDLC_REASONING_MODEL:-…}` /
  `${SDLC_WRITING_MODEL:-…}` resolve via this feature.
- **Minder** — can migrate `${model.<tier>}` to `${MINDER_REASONING_MODEL:-…}` and delete the
  bespoke model-context plumbing.
