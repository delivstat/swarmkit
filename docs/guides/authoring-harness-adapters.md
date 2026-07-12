# Authoring a harness adapter

SwarmKit runs external agentic harnesses (Claude Code, Codex, opencode, Gemini CLI, …) as **executors**. A harness is added as **data** — an `adapter.yaml` — not code. This guide shows how to write one.

See also: the schema at `packages/schema/schemas/executor-adapter.schema.json`, the design note `design/details/executor-declarative-adapters-plan.md`, and the bundled reference adapters under `packages/runtime/src/swarmkit_runtime/executors/adapters/` (start by copying the closest one).

## The mental model

A harness is a subprocess that:

1. is launched with a **command line** (`launch`),
2. emits **line-delimited JSON** on stdout as it works (`stream: {format: jsonl}`),
3. which the adapter maps into SwarmKit's normalized `ExecEvent` vocabulary (`event_map`).

Once mapped, every harness is observed identically — cost, trace, audit — and a topology archetype selects it with `executor: {kind: <adapter id>}`.

## Where adapters live

- **Bundled** (shipped + vetted): `packages/runtime/src/swarmkit_runtime/executors/adapters/*.yaml`.
- **Workspace** (yours): `<workspace>/adapters/*.yaml`. A workspace adapter may override a bundled kind, and is subject to the [launch review gate](#the-launch-review-gate).

## Anatomy of an adapter

```yaml
apiVersion: swarmkit/v1
kind: ExecutorAdapter
metadata:
  id: my-harness            # the executor `kind` an archetype selects (lowercase-kebab)
  name: My Harness
  description: One line on what it is + whether it's verified against a real binary.
spec:
  # 1. LAUNCH — argv (NO shell). Closed substitution vars only.
  launch:
    command: [my-harness, run, "{task.statement}", --json]
    optional_args:                       # appended only when the var is set
      - when: budget.max_turns
        args: [--max-turns, "{budget.max_turns}"]
      - when: config.model
        args: [--model, "{config.model}"]
  # 2. AUTH — declare both modes; each contributes env / args / credential_paths (generic).
  auth:
    default: subscription
    modes:
      api_key:
        env: { MY_HARNESS_API_KEY: "{credential.model_provider}" }
      subscription:
        credential_paths: [~/.my-harness]
  # 3. STREAM
  stream: { format: jsonl, retain_raw: false }
  # 4. EVENT_MAP — one parsed JSON line -> zero or more ExecEvents
  event_map:
    - when: { type: session }            # literal-equality match on dotted paths
      set: { session_id: "$.id" }        # capture (for resume); emits nothing
    - when: { type: message }
      emit:
        - event: message
          with: { role: assistant, text: "$.text" }
    - when: { type: tool }
      emit:
        - event: tool_call
          with: { tool: "$.name", input_summary: "$.args" }
    - when: { type: usage }
      emit:
        - event: usage
          with: { input_tokens: "$.tokens.in", output_tokens: "$.tokens.out", cost_usd: "$.cost" }
    - when: { type: done }
      emit:
        - event: result
          with: { status: success, output: "$.text" }
  # 5. RESUME (optional) — the captured session id replayed on retry
  resume: { arg: [--session, "{resume.token}"] }
  # 6. Terminal + interaction + profile
  success_when: { exit_code: 0 }
  on_unanswerable: abort                  # deny | abort only (see interaction ceiling)
  artifacts: { profile: files }           # files | structured | media
provenance:
  authored_by: human
  version: 0.1.0
```

## The event-map DSL (deliberately minimal)

- **Input:** line-delimited JSON, one object per line.
- **`when` (match):** literal equality on dotted field paths (`{type: message}`, `{part.reason: stop}`). No regex, no conditionals.
- **`$.a.b.c` (extract):** dotted paths + array indices. `for_each: "$.items"` iterates one array, running the rule's `emit` per item.
- **`set`:** capture into adapter state — only `session_id` is recognized (for resume tokens).
- **`emit`:** build an `ExecEvent`. `with` maps event fields to extractions/literals, or `{from: "$.x", map: <table>}` to translate a vendor enum via a named `status_map`.
- **Substitution vars (closed set):** `{task.statement}`, `{task.base_ref}`, `{sandbox.root}`, `{budget.max_turns|max_cost_usd|max_wall_clock_minutes}`, `{resume.token}`, `{config.*}`, `{credential.model_provider}`. Substituted into argv positions only — **never** a shell string.

### Classifying result status across two fields

If a harness signals errors with more than one field (Claude Code emits `subtype: success` **and** `is_error: true` on failures), use **ordered rules — the last matching result wins**:

```yaml
    - when: { type: result }
      emit: [{ event: result, with: { status: success } }]
    - when: { type: result, is_error: true }
      emit: [{ event: result, with: { status: failure } }]
    - when: { type: result, subtype: error_max_turns }
      emit: [{ event: result, with: { status: budget_exceeded } }]
```

## The interaction ceiling

Declarative adapters are capped at `on_unanswerable: deny | abort`. Mid-run `relay` (pause for human approval and feed the answer back into a live session) needs bidirectional session control a subprocess+JSONL map can't express — that's a code-tier feature. The never-hang guarantee still holds via the budget/idle envelope.

## Verify it against the real binary

Fixture tests prove your mapping is internally consistent; they do **not** prove it matches the real tool. **Always run the real harness once** and confirm the events normalize. Capture a real stream:

```bash
my-harness run --json "say PONG"   # inspect the actual JSON lines, fix your event_map to match
```

Then, with the binary on PATH, the gated e2e proves the whole pipeline:

```bash
SWARMKIT_E2E=1 uv run pytest packages/runtime/tests/test_harness_e2e.py
```

Mark an adapter **EXPERIMENTAL** in its `description` until it's been run against a real binary.

## Auth: API key vs subscription

Declare **both** modes. Each mode generically contributes to the launch — an `env` var, extra `args`, and/or `credential_paths` provisioned into the sandbox. At run time only the **active** mode's credentials are effective: the engine strips every *other* mode's env vars, so a stale `ANTHROPIC_API_KEY` in your shell can't override a subscription login. Set `auth.default`; override per-archetype with `config.auth_mode`.

## The launch review gate

A **workspace** adapter's `launch` block is a command line run on your host, so it must be human-approved before it can run — and re-approved on any change:

```bash
swarmkit adapters list                 # shows workspace adapters + approval status
swarmkit adapters show my-harness      # inspect the exact command + fingerprint
swarmkit adapters approve my-harness   # record approval (a human-only action)
```

Approval stores a fingerprint of the executable surface in `<workspace>/.swarmkit/adapters-approved.json`; editing the `launch` invalidates it. Bundled reference adapters are pre-vetted and bypass the gate. No agent can approve a launch — it's a scope reserved for human identity.

## Persistent working directory

By default a harness runs in an ephemeral, isolated git worktree (produce a diff, never integrate). For a session-scoped harness whose memory is keyed to cwd (Claude Code stores project memory under `~/.claude/projects/<cwd-slug>/`), set a persistent directory so memory accumulates across runs:

```yaml
executor:
  kind: claude-code
  config:
    working_dir: coding-worker   # persistent (under the workspace root); omit for the isolated worktree
```

## Sandbox and isolation (opt-in)

By default a harness runs in an ephemeral git worktree — isolation of *edits*, not of the *process*: the subprocess still inherits your host network and, with a `working_dir`, host files. For untrusted harness code you can opt into a **container tier** with resource limits and enforced egress. It is **off by default** — an adapter with no `sandbox` block behaves exactly as before.

```yaml
spec:
  # …launch / stream / event_map…
  sandbox:
    kind: container            # worktree (default) | container
    image: my-harness:latest   # a prebuilt image you trust; OR use `build:` below
    network: allowlist         # deny (default) | allowlist
    allow: [api.anthropic.com]  # hosts reachable when network: allowlist
    mounts:                    # extra resources beyond the worktree (KB, shared config)
      - { source: ./knowledge, target: /knowledge, mode: ro }
    resources: { cpus: "2", memory: 2g, pids: 512 }
```

**Run the harness with no local install.** Instead of a prebuilt `image`, declare how to `build` one — the runtime builds a derived image **once** (content-addressed, cached) and the user brings only their API key/subscription (injected at run, never baked in):

```yaml
    sandbox:
      kind: container
      build:
        base: node:22-slim
        install: ["npm install -g @anthropic-ai/claude-code"]
```

`build` takes exactly one front-end: `base` (+ optional `install`, the self-contained path, lowered to a Dockerfile internally), `dockerfile` (a path, for full Docker control), or `dockerfile_inline` (Dockerfile content inline). `image` and `build` are mutually exclusive.

**On the image:** SwarmKit publishes and requires no bespoke image — `build` from any public base, point `image:` at any image, or set `$SWARMKIT_HARNESS_IMAGE`. None of the three, with `kind: container`, is a clear error, not a guessed base.

**Egress is enforced, not advised.** `network: deny` → no outbound access (a local-model harness); `network: allowlist` → only `allow` hosts, via a managed proxy (the mode a cloud harness needs to reach its model API and nothing else). HTTP/SSE MCP servers become reachable by adding their `host:port` to `allow`.

**The disable switch always wins.** `SWARMKIT_DISABLE_CONTAINER_SANDBOX=1` forces the native worktree for every archetype regardless of adapter config — the escape hatch for a box with no container runtime or a fast local loop. A container requested with no runtime present (and no disable) is a clear error, never a silent unsandboxed run. Tune limits per archetype without forking the adapter via `executor.config.sandbox`.

> **Status:** the `sandbox` config surface is stable (schema ≥ 1.16.0); the enforcing container provisioner (build, mounts, egress proxy) is rolling out across releases. Until it lands in your build, `kind: container` errors loudly with the disable-switch hint — use the worktree default meanwhile. See `design/details/executor-container-sandbox.md`.

## When the DSL isn't enough

If your harness needs something past the DSL ceiling — non-JSONL output, stateful stderr parsing, bidirectional interaction — declare `spec.requires: code` and implement a Tier-1 Python `Executor` instead. That's the escape hatch; the common subprocess+JSONL shape (every major coding harness) needs none.
