# Artifact env-variable substitution (runtime)

**Scope:** runtime (artifact load path)
**Status:** proposed

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

## Rules

- **Fail loud on an undefined variable with no default.** A missing `${VAR}` (no `:-default`) is a
  configuration error → raise, don't silently expand to empty. (This tightens the current MCP
  behaviour, which warns + empties; that lenient path stays for backward-compat where it is already
  relied on, or is migrated in the same change — see test plan.)
- **Load-time, once.** Substitution runs when the artifact text is loaded, before schema validation,
  so validators + the runtime see resolved values. Applied to string scalars anywhere in the tree.
- **Strings only.** Numbers/bools are untouched; a `${VAR}` only expands inside a string value.
- **No recursion.** The result of a substitution is not itself re-scanned (avoids surprise + loops).

## Where it hooks

A single load-time pass at the artifact-load choke point: after `yaml.safe_load`, walk the parsed
structure and expand every string scalar (or expand on the raw text before parse — decided in the
design/impl, leaning post-parse walk so escaping + non-string values are unambiguous). Reuse the
existing `${…}` regex from `mcp/_client`. The MCP env path collapses onto the same helper.

## Non-goals

- Not a templating language (no conditionals, loops, or arithmetic) — just variable substitution
  with defaults.
- Not secret management — values still come from the environment; this only references them.
- Not the Minder `${model.<tier>}` context — that indirection is superseded by plain `${ENV_VAR}`
  refs (Minder can migrate its archetypes to env refs + drop the bespoke context).

## Test plan

- **Basic:** `${VAR}` expands; `${VAR:-default}` uses the default when unset and the value when set.
- **Fail-loud:** an undefined `${VAR}` with no default raises a clear error naming the var + artifact.
- **Escape:** `$${X}` yields literal `${X}`.
- **All kinds:** substitution applies in topology, skill, archetype, workspace, trigger fixtures.
- **Order:** substitution happens before schema validation (a `${VAR}` that resolves to an invalid
  value is caught by the validator, not silently accepted).
- **MCP parity:** existing MCP env expansion still works through the unified helper.

## Demo plan

`just demo-env-substitution` (or a script): load an archetype whose `model.name` is
`${DEMO_MODEL:-default-model}`, show it resolving to the default with the var unset and to the
override with it set, and show a missing-no-default ref failing loud.

## Consumers

- **SDLC archetype library** (`examples/sdlc-pipeline/`) — `${SDLC_REASONING_MODEL:-…}` /
  `${SDLC_WRITING_MODEL:-…}` resolve via this feature.
- **Minder** — can migrate `${model.<tier>}` to `${MINDER_REASONING_MODEL:-…}` and delete the
  bespoke model-context plumbing.
