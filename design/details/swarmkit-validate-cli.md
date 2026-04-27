---
title: `swael validate` CLI + human-readable error rendering
description: First real CLI command. Resolves a workspace, prints a resolved tree on success, prints structured + human-friendly errors on failure. Closes tasks #31 (CLI) and #23 (error rendering) — the usability-first landing promised in docs/notes/llm-friendly-knowledge.md.
tags: [cli, m1, usability, validate]
status: in-review
---

# `swael validate` CLI + error rendering

**Scope:** `packages/runtime/src/swael_runtime/cli/` (CLI + renderer).
**Design reference:** `design/details/topology-loader.md` (the resolver this CLI drives), `docs/notes/usability-first.md`, `docs/notes/llm-friendly-knowledge.md` ("errors are docs").
**Status:** in review — bundles tasks #23 and #31.

## Goal

A first-time user runs `swael validate <path>` on their workspace and gets:

- On success, a concise tree showing what Swael understood: topologies, agents (with merged model / archetype / skills), registry sizes.
- On failure, **actionable** errors — not raw jsonschema traces. Each error states what went wrong, where in the YAML, which rule was violated, and a suggested remediation. The user fixes and re-runs without consulting the design doc.

This is the first CLI landing that a real user actually sees. It sets the DX bar for every subsequent CLI command (`run`, `serve`, `author`, `ask`). Every decision here is a template for later.

## Non-goals

- **Running topologies.** `swael run` is M3. Validation is static.
- **Fix-mode.** `swael validate --fix` that rewrites YAML is tempting but out of scope — automatic fixes silently change user intent.
- **Schema editing helpers.** "Add audit block to this skill" is a future `swael author` improvement.

## User-facing shape

```
swael validate [PATH] [OPTIONS]

Arguments:
  PATH                         Workspace root (default: current directory).

Options:
  --json                       Emit JSON instead of human-formatted output.
                               Every error on its own line (JSONL), plus a
                               final summary object. Shell-pipeable.
  --tree                       On success, include a fully-expanded agent
                               tree. Verbose.
  --quiet / -q                 Suppress the success summary; only print on
                               error. Exit code still reflects outcome.
  --color / --no-color         Override TTY auto-detection for coloured
                               output.
  --help                       Show usage.
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Resolution succeeded (no errors). |
| `1` | One or more `ResolutionError`s. User-correctable. |
| `2` | Usage error (bad flag, non-existent path). |

Matches `kubectl`, `terraform plan`, `helm lint` conventions.

## Success output (human)

Default — concise summary. A real workspace is a lot of lines; a first-time user wants confidence, not noise.

```
✓ workspace: full-workspace
  topologies: 1   (hello)
  skills:     1   (audit-log-write)
  archetypes: 1   (supervisor-root)
  triggers:   2   (hello-webhook, hello-daily)

no errors, 0 warnings
```

With `--tree`, print the resolved agent tree inline:

```
✓ workspace: full-workspace

topology: hello
  root (supervisor-root)
    model: anthropic/claude-sonnet-4-6
    skills: []

no errors, 0 warnings
```

## Success output (JSON)

JSONL — one summary object per line so `jq` and shell pipes work naturally.

```json
{"event":"validate.ok","workspace":"full-workspace","topologies":1,"skills":1,"archetypes":1,"triggers":2}
```

With `--tree`, append per-topology objects:

```json
{"event":"validate.topology","id":"hello","root":{"id":"root","role":"root","archetype":"supervisor-root","model":{"provider":"anthropic","name":"claude-sonnet-4-6"},"skills":[],"children":[]}}
```

## Error output (human)

Each error is one block. Title, location, rule, suggestion. Coloured on TTY: red "error", dim rule + location lines, bold title.

```
error: skill 'not-a-real-skill' is not defined in this workspace
  at  packages/runtime/tests/fixtures/.../bad.yaml
      /defaults/skills/0
  rule  archetype.unknown-skill
  try   Define a skill with id='not-a-real-skill', or change the reference to an existing one.
        You can also use an abstract placeholder
        ({ abstract: { category, capability? } }) if the archetype should
        be concrete-skill-agnostic.
```

Multiple errors: one block each, separated by a blank line. Final summary:

```
3 errors across 2 files. See design/details/topology-loader.md for the
error code reference, or run `swael ask "explain <error-code>"`.
```

### Design choices made explicit

- **"error"** in lowercase, not "ERROR". Reads as a sentence, not a shout.
- **"try"** not "suggestion" — shorter, action-oriented.
- **Workspace-relative path** when the error's artifact is inside the workspace; absolute otherwise. Makes copy-paste to an editor work.
- **Rule code in a separate line**, always the machine-readable form. Grep-friendly.
- **Multi-line suggestions wrap at the "try" column** (8 chars), not the terminal width.

## Error output (JSON)

One JSON object per error on stdout (JSONL):

```json
{"event":"validate.error","code":"archetype.unknown-skill","message":"...","artifact_path":"...","yaml_pointer":"/defaults/skills/0","rule":"archetype.unknown-skill","suggestion":"...","related":[]}
```

Then a final summary:

```json
{"event":"validate.summary","status":"failed","errors":3,"files_affected":2}
```

Piping patterns the JSON enables:

```bash
# Count errors
swael validate --json | jq '[.[] | select(.event=="validate.error")] | length'

# Filter to one code
swael validate --json | jq 'select(.code=="archetype.unknown-skill")'

# Per-file counts
swael validate --json | jq -s 'map(select(.event=="validate.error")) | group_by(.artifact_path) | map({path:.[0].artifact_path, count:length})'
```

## Render precedence

Each error has a rendered form chosen by code. Where no specific renderer exists, fall back to the generic "message + rule + suggestion" layout. Specific renderers add context:

| Code | Extra context |
|---|---|
| `workspace.yaml-parse` | Include the offending line if the error carries one |
| `agent.abstract-ambiguous` | List the candidate skill IDs as a bullet list |
| `agent.abstract-no-match` | List "skills available in this workspace" grouped by category |
| `skill.composed-cycle` | Render the cycle as `a → b → a` in the message |

The renderer is a dispatch table keyed by `ResolutionError.code`. Adding a specific renderer is adding one entry.

## Tree rendering

When `--tree` is requested on a valid workspace, use `rich.tree.Tree` (already in runtime deps). Structure:

```
workspace full-workspace
├── topology hello
│   └── root (role=root, archetype=supervisor-root)
│       └── model: anthropic/claude-sonnet-4-6
└── triggers (2)
    ├── hello-webhook → [hello]
    └── hello-daily  → [hello]
```

Skill / archetype registries are implicit from agent references; we don't dump them separately in tree mode.

## Implementation sketch

```
packages/runtime/src/swael_runtime/cli/
├── __init__.py           # Typer app, validate() command wired
├── _render.py            # render_error, render_success, render_tree
└── _output.py            # json vs. text dispatch; color detection
```

Key functions:

```python
# cli/_render.py
def render_error(err: ResolutionError, *, color: bool) -> str: ...
def render_summary(n_errors: int, n_files: int) -> str: ...
def render_success(ws: ResolvedWorkspace, *, tree: bool, color: bool) -> str: ...

# cli/_output.py
class Output(Protocol):
    def emit(self, payload: dict[str, Any]) -> None: ...  # JSON
    def write(self, text: str) -> None: ...               # text

def make_output(json_mode: bool, color: bool) -> Output: ...
```

The `validate` Typer command is ~30 lines: parse args, build `Output`, call `resolve_workspace`, dispatch on success / `ResolutionErrors`, exit with the right code.

## Test plan

- **Unit — renderer:** one test per error code with a specific renderer; golden-text snapshots per case. `--color` off (deterministic strings). Each test constructs a known `ResolutionError` and asserts the exact rendered text.
- **Unit — output dispatch:** `--json` mode emits JSONL matching the documented shape.
- **Integration — CLI:** use Typer's test runner. For every fixture under `packages/runtime/tests/fixtures/workspaces/` assert exit code 0 and non-empty tree. For every `workspaces-invalid/` fixture assert exit code 1 and a rendered error matching the expected code.
- **Exit codes:** verify 0 / 1 / 2 all reachable.
- **Colour detection:** mock `sys.stdout.isatty()` to force both branches.

## Demo plan

- `just demo-validate` (lands with this PR): runs `swael validate` against every valid + invalid fixture workspace, prints outputs. First fixture prints the success tree; invalid fixtures print human-readable error blocks. The demo itself is the exit criterion for task #31.
- PR body includes a terminal transcript of running against a deliberately-broken workspace.

## Accessibility / usability

- **Every error has a suggestion.** If a new error code emerges without one, flag it in review; default fallback suggestion must point at `swael ask "explain <code>"` (once M4 ships) rather than "check the docs."
- **Colour is optional.** `NO_COLOR` env var respected. `--no-color` flag respected. Output must be readable in plain ASCII.
- **Internationalisation.** v1.0 is English-only. Don't bake strings into `ResolutionError.message`; keep them in `_render.py` so a future v1.x can swap based on locale. (For now this means messages live twice; acceptable.)

Actually, **push-back on myself here**: building i18n infrastructure now is over-engineering for a framework that hasn't shipped v1.0. **Scratch i18n from v1.0 scope.** Document it as a known limitation; revisit if real demand emerges.

## Follow-ups (separate PRs, tracked as tasks)

- When M4 lands `swael ask`, update the summary footer to suggest `swael ask "explain <code>"` instead of pointing at the docs file.
- When M2 lands the governance layer, add `swael validate --strict` that runs a stricter set of checks (e.g. scope coverage for every skill an agent invokes).
- Example workspace (`examples/hello-swarm/`) under task #32 — separate PR.
