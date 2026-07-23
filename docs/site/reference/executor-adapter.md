# Executor adapter

An **executor adapter** is a first-class SwarmKit artifact (`kind: ExecutorAdapter`) that teaches SwarmKit how to run an external agentic **harness** — Claude Code, Codex, OpenCode, or any goal-pursuing subprocess — as a node executor. It is a **declarative** adapter: a subprocess launch template plus a mapping from the harness's line-delimited JSON output into SwarmKit's normalized `ExecEvent` vocabulary. A new harness is added as *data* (this artifact), with no Python and no runtime release.

The provider-seam placement, the two-tier adapter model, the normalized event schema, and the mid-run interaction model are specified in the [executor abstraction design note](https://github.com/delivstat/swarmkit/blob/main/design/details/executor-abstraction.md). This page is the artifact reference.

## Executor vs. skill

An executor answers *how a node does its work*; a skill answers *what capability an agent may invoke*. The dividing rule: **if it produces a diff or holds a session, it is an executor; if it answers a question and returns, it may be a skill.** `executor` is a node-execution provider seam alongside `ModelProvider` and `GovernanceProvider` — not a parallel capability primitive.

## Two ways a harness enters a topology

1. **The archetype selects an executor.** An archetype's optional `executor` block declares how its nodes run:

   ```yaml
   executor:
     kind: harness          # model (default) | harness | <plugin-registered kind>
     ref: claude-code       # for kind: harness, the adapter id (required)
     version_constraint: ">=2.1"   # optional; interpreted by the adapter
     config:                # opaque to core; validated by the adapter's own schema
       permission_mode: bare
   ```

   `executor` is **optional and backward-compatible**: absent means `kind: model` with the archetype's `defaults.model`. `kind` is not a closed enum — it is validated against the executor registry at runtime.

2. **The adapter itself is the `ExecutorAdapter` artifact.** It lives in the workspace (e.g. an `adapters/` directory) and is what `ref:` resolves to.

## Adapter fields

Required top-level: `apiVersion`, `kind`, `metadata`, `spec`, `provenance`. Within `spec`, required: `launch`, `stream`, `event_map`.

| Field (`spec.`) | Required | What it does |
|---|---|---|
| `launch` | yes | How to launch the subprocess. `command` is argv (no shell); values are templated with a closed variable set (`{task.statement}`, `{sandbox.root}`, `{budget.max_turns}`, `{credential.model_provider}`, `{config.*}`, …). `optional_args` append arg-groups only when a variable is set; `env` injects env vars. |
| `stream` | yes | `format: jsonl` (line-delimited JSON only). `retain_raw: true` tees each untranslated vendor line as `exec.raw`. |
| `event_map` | yes | Rules that match a parsed JSON line (literal-equality on dotted paths), optionally `for_each` an array, `set` state (only `session_id`), and `emit` `ExecEvent`s (`started`, `message`, `tool_call`, `artifact`, `usage`, `approval_requested`, `input_requested`, `result`, `raw`). Field values are `$.dotted.path` extractions or literals; `{from, map}` translates through a named `status_map`. |
| `auth` | no | Which auth modes the harness supports (`api_key`, `subscription`), expressed generically as env vars, args, and/or `credential_paths`. |
| `status_map` | no | Vendor discriminator → `ExecResultStatus` (`success`/`failure`/`budget_exceeded`/`cancelled`/`needs_approval`/`stalled`); `_default` covers the rest. |
| `resume` | no | Makes resume-token support declarative — replay the captured `session_id` into a retry/resume launch. |
| `success_when` | no | Terminal success predicate (`exit_code`). Core layers a semantic check (typed output + artifact-manifest match) on top — exit code alone is necessary, not sufficient. |
| `on_unanswerable` | no | `deny` \| `abort` (default) \| `relay` — how a mid-run request outside the launch grant is handled. `relay` requires an `interaction` block. |
| `interaction` | conditional | Required when `on_unanswerable: relay`. `driver: hold-stream \| park-resume`; optional `max_approval_wait_seconds` (never-hang bound). |
| `sandbox` | no | Isolation tier. Absent ⇒ native git-worktree (default). `kind: container` runs the harness in docker/podman with an `image` or a `build`, `mounts`, a `network` policy (`deny` default \| `allowlist`), and `resources`. |
| `telemetry_grade` | no | `normalized` (default) \| `opaque`. Opaque (unobservable) adapters are denied by default and need explicit per-archetype opt-in. |
| `requires` | no | `code` — set only when the adapter has hit the declarative DSL ceiling and must graduate to a Tier-1 Python executor. |

The DSL is deliberately minimal: JSONL only, literal-equality matching, dotted-path extraction, one named enum-translation map. Mid-run `relay` interaction is the single Tier-1 seam.

## Schema shape

```yaml
apiVersion: swarmkit/v1
kind: ExecutorAdapter
metadata:
  id: <lowercase-kebab>        # this is the executor kind/ref an archetype selects
  name: <human name>
  description: <what harness this launches>   # min 10 chars
spec:
  launch:
    command: [<argv template>, ...]           # no shell; value-only substitution
    optional_args:
      - when: budget.max_turns
        args: ["--max-turns", "{budget.max_turns}"]
    env:
      SOME_CONFIG: "{config.foo}"
  auth:
    default: api_key
    modes:
      api_key:
        env: { ANTHROPIC_API_KEY: "{credential.model_provider}" }
      subscription:
        credential_paths: ["~/.claude"]
  stream:
    format: jsonl                              # jsonl only
    retain_raw: true
  event_map:
    - when: { type: assistant }
      emit:
        - event: message
          with: { role: assistant, text: "$.message.content" }
    - when: { type: result }
      set: { session_id: "$.session_id" }
      emit:
        - event: result
          with:
            status: { from: "$.subtype", map: status_map }
            cost_usd: "$.total_cost_usd"
  status_map: { success: success, error_max_turns: budget_exceeded, _default: failure }
  success_when: { exit_code: 0 }
  sandbox:
    kind: worktree                             # worktree (default) | container
  telemetry_grade: normalized
provenance:
  authored_by: human
  version: 1.0.0
```

## Authoring an executor adapter

The `launch` block is the sharpest edge (it is a command line): declarative adapters carry a mandatory human-review gate on first approval and on any change to `launch`, regardless of workspace auto-run trust. Keep to the DSL ceiling — if you need resume logic beyond a token replay, bidirectional streaming, or non-line output, declare `requires: code` and graduate to a Tier-1 Python executor. `get_schema("executor-adapter")` returns the exact shape for the conversational authoring path.

## See also

- [Executor abstraction design note](https://github.com/delivstat/swarmkit/blob/main/design/details/executor-abstraction.md) — provider-seam placement, `ExecEvent` vocabulary, mid-run permission/input handling.
- [Archetypes catalogue](archetypes.md) — the `executor` block lives on an archetype.
