---
status: draft
---

# Executor P3 — declarative adapter foundation (harnesses are data, not code)

Reorders the RFC (`executor-abstraction.md`): pulls the **Tier-2 declarative adapter engine**
(RFC §5.2, phased P4) forward to be *the* foundation, and drops the planned `codex` **code**
adapter. Direction (owner, this session): **no harness-specific Python.** A new harness — codex,
opencode, gemini-cli, anything that spawns a subprocess and emits line-delimited JSON — is added by
writing an `adapter.yaml`, with **zero code and zero release.** SwarmKit ships a library of ready-made
adapter YAMLs for the big harnesses so anyone uses them as-is.

This makes executors *data*, the same way topologies are data — the framework's first pillar, applied
one level down.

## What P2 left, and the one thing it got wrong

P2 shipped the runtime contract a harness targets: the `ExecEvent` vocabulary, `Executor.run()`,
worktree sandbox, budget/liveness envelope, deny/abort interaction, observability parity. All of that
is adapter-agnostic and stays.

The wrong part: P2's `claude-code` adapter is **hardcoded Python** (`executors/_claude_code.py`), and
`_harness_node._build_executor` is a literal `if kind == "claude-code"` switch. That is exactly the
"write code per harness" this phase removes. **`_claude_code.py` is deleted** and re-expressed as a
bundled `claude-code.yaml` — the reference harness becomes data like every other.

## The artifact: `executor-adapter` (adapter.yaml)

A new first-class workspace artifact, resolved by the same pipeline as skills/archetypes/topologies
(`resolver.discover` → validate against a canonical schema → build a registry). Bundled reference
adapters ship as package data under `packages/runtime/src/swarmkit_runtime/executors/adapters/`; a
workspace may add its own under `<workspace>/adapters/*.yaml`.

```yaml
apiVersion: swarmkit/v1
kind: ExecutorAdapter
metadata:
  name: claude-code            # this is the executor `kind` an archetype selects
spec:
  # 1. LAUNCH — argv template. Closed substitution vars only; NO shell interpolation.
  launch:
    command:
      - claude
      - -p
      - "{task.statement}"
      - --output-format
      - stream-json
      - --verbose
    # args appended only when the referenced value is set (else the whole group drops)
    optional_args:
      - when: budget.max_turns
        args: [--max-turns, "{budget.max_turns}"]
      - when: config.model
        args: [--model, "{config.model}"]
    # the ONE allowed secret (§7): the model-provider credential, env-injected + scrubbed
    env:
      ANTHROPIC_API_KEY: "{credential.model_provider}"
  # 2. STREAM — how to read stdout
  stream:
    format: jsonl              # line-delimited JSON only (RFC decision 1a)
    retain_raw: false          # tee vendor lines as exec.raw when true
  # 3. EVENT_MAP — vendor JSON line -> zero or more ExecEvents
  event_map:
    - when: { type: system }              # literal equality on dotted field paths
      set: { session_id: "$.session_id" } # capture into adapter state (no event)
    - when: { type: assistant }
      for_each: "$.message.content"       # iterate an array field
      emit:
        - when: { type: text }
          event: message
          with: { role: assistant, text: "$.text" }
        - when: { type: tool_use }
          event: tool_call
          with: { tool: "$.name", input_summary: "$.input" }
    - when: { type: assistant }
      emit:
        - event: usage
          with:
            input_tokens: "$.message.usage.input_tokens"
            output_tokens: "$.message.usage.output_tokens"
    - when: { type: result }
      emit:
        - event: usage
          with: { input_tokens: "$.usage.input_tokens", cost_usd: "$.total_cost_usd" }
        - event: result
          with:
            status: { from: "$.subtype", map: status_map }
            output: "$.result"
  status_map:                  # vendor discriminator -> ExecResultStatus
    success: success
    error_max_turns: budget_exceeded
    _default: failure
  # 4. RESUME — makes resume-token support declarative, not Tier-1 code
  resume:
    arg: [--resume, "{resume.token}"]     # session_id captured above, replayed on retry
  # 5. Terminal + interaction + profile
  success_when: { exit_code: 0 }          # core layers the semantic artifact check on top
  on_unanswerable: abort                  # declarative adapters: deny | abort only (see below)
  telemetry_grade: normalized             # `opaque` denied by default (RFC decision 5)
  artifacts: { profile: files }           # files | structured | media
```

### The DSL is deliberately minimal (RFC decision 1a)

- **Input:** line-delimited JSON only. One line → one parsed object.
- **Matching (`when`):** literal equality on dotted field paths. No regex, no conditionals.
- **Extraction (`$.a.b.c`):** dotted paths + array indexing; `for_each` iterates one array. No filter
  expressions, no multi-line aggregation.
- **`map`:** a named lookup table translates a vendor enum to an `ExecEvent` field (the only
  "logic"). `_default` covers the rest.
- **Substitution vars (closed set):** `{task.statement}`, `{task.base_ref}`, `{sandbox.root}`,
  `{budget.max_turns|max_cost_usd|max_wall_clock_minutes}`, `{resume.token}`, `{config.*}` (adapter
  knobs), `{credential.model_provider}` (env only). A var that expands empty drops its `optional_args`
  group. Values are substituted into argv positions — **never** concatenated into a shell string.

Anything past this ceiling declares `requires: code` and graduates to a Tier-1 Python `Executor`
(still supported as an escape hatch — the base class and registry are unchanged). The point is that
the *common* subprocess+JSONL shape, which is every major coding harness, needs none.

### Auth — both modes, expressed generically (RFC decision 4)

A harness authenticates two ways, and an adapter declares **both**: **API key** (the model-provider
credential) and **subscription** (saved CLI login / long-lived setup token). This is *not* a
special-cased mechanism — each mode simply declares what it **contributes to the launch**: `env` vars,
extra command `args`, and/or `credential_paths` provisioned into the sandbox. So auth may be an
environment variable, a `--api-key` command flag, a mounted `~/.claude` credential dir, or any
combination — the engine merges the active mode's contribution and has no per-mode logic. The
workspace sets `auth.default`; an archetype may override; in headless mode `api_key` takes precedence
where both are usable (deterministic). The one credential-rule exception (§7) holds: the
model-provider key is the only secret in the launch, `{credential.model_provider}`, scoped per-run and
scrubbed. `claude-code.yaml` ships declaring both modes.

### Interaction ceiling

Mid-run `relay` (pause for human approval, feed the answer back into the live session) and
`input_escalation` (route a domain question to the lead node) need **bidirectional session control**
a declarative subprocess can't do generically — RFC §5.2 makes them Tier-1-only. So declarative
adapters are capped at `on_unanswerable: deny | abort`. **The never-hang guarantee is unaffected**
(P2's budget/idle envelope + abort already deliver it). The interactive tier is deferred — see below.

## PR sequence (dependency-ordered)

1. **`executor-adapter` artifact — schema + discovery + validation.** New canonical
   `packages/schema/schemas/executor-adapter.schema.json` (+ pydantic + TS via codegen, per
   `docs/notes/schema-change-discipline.md`). Register the `ExecutorAdapter` kind in
   `resolver.discover`. Parse + validate only; no engine yet. Tests: valid/invalid adapter fixtures.
2. **The event-map interpreter (pure).** `executors/_adapter_spec.py` (typed spec from the parsed
   YAML) + `executors/_event_map.py` (JSONL line → `when`/`for_each`/`set`/`emit`/`map` → ExecEvents)
   + the argv template substitutor (closed vars, no shell). Pure, fixture-tested against real
   stream-json lines. This is the heart; no subprocess yet.
3. **`DeclarativeExecutor(Executor)` + registry loading.** The Executor wrapping the interpreter:
   `preflight` (launch binary resolvable), `run` (spawn subprocess → stream lines → interpreter →
   `ExecStarted`/events/`ExecRaw`), `cancel`, `resume_token` (the declared session field). Build an
   `ExecutorRegistry` entry per discovered/bundled adapter; **`_build_executor` becomes a registry
   lookup — delete the `if kind == "claude-code"` switch.** Tests: run() over a scripted stream.
4. **Migrate `claude-code` to YAML; delete `_claude_code.py`.** Author bundled `claude-code.yaml`.
   Re-point the P2 stream-json fixture test at the declarative adapter — same bytes in, same
   `ExecEvent`s out — proving the migration is behavior-preserving. Delete the Python adapter + its
   test. resume via the declared `session_id`.
5. **Bundled reference adapter library: `codex`, `opencode`, `gemini-cli`.** Pure YAML + a captured
   stream fixture per harness asserting normalization. **Zero core code** — this is the proof of the
   contract (RFC acceptance #3) and the "usable as-is by anyone" deliverable. A demo topology routes
   one slice to a harness; per-node cost is visible from `exec.usage` (already wired, PR7 of P2).
6. **Launch-block review gate + contributor guide.** The `launch` block is the sharpest edge (it is a
   command line). Mandatory human-review on first approval and on any `launch` change, via the
   existing `ReviewQueue` — regardless of workspace auto-run trust (RFC §5.2). Docs: a contributor
   guide for authoring an `adapter.yaml`, and the `docs/notes/` discipline entry (touch schema →
   regen validators → bundled adapters).

## Governance (AGT), every PR that touches it

The `launch` command line is reviewed like any capability grant — a scope reserved for human
identity, no agent can self-approve it (CLAUDE.md invariant #6). A harness node stays node-visible in
audit (archetype, capability set, budget). The two-class credential rule holds: only the
model-provider key is env-injected (`{credential.model_provider}`) and scrubbed; no other secret
enters the executor environment (the egress proxy is still deferred, so declarative harness nodes get
*no* non-model secrets).

## Explicitly deferred — the interactive tier (a later phase)

Everything that needs bidirectional session control or a running-topology feedback loop, unchanged
from the RFC's intent but now clearly *after* the declarative foundation: `on_unanswerable: relay` +
the cockpit approval inbox; `exec.input_requested` + lead-node escalation + memoization; the shared
core question **classifier**; trust-accrual → allowlist changeset proposals; cross-boundary W3C
`traceparent` propagation + gate/escalation spans; the egress proxy + container sandbox + network
allowlists. The seams for these exist (checkpointer/resume, `ReviewQueue`, `current_parent_agent`,
structured-output `response_format`, the `RecordedSpan` tree) — mapped, not built.

## Acceptance (subset of RFC §10, for this phase)

- Existing workspaces run unmodified; `executor` absent ⇒ model behavior (still true).
- A new harness is added as an `adapter.yaml` with **no core code change** and runs end-to-end
  (opencode or gemini-cli is the proof).
- `claude-code` runs identically to P2 with the Python adapter deleted (same fixture → same events).
- Schema rejects an adapter with a malformed `launch`/`event_map`; an archetype selecting an unknown
  `kind` fails resolution.
- A budget breach and an out-of-grant `deny`/`abort` on a declarative harness still never hang
  (regression against the headless-hang mode).
- One OTel `executor` span per harness node with `executor.kind`/`ref` + cost (already shipped).

## Test / demo plan

- **Unit:** the event-map interpreter against captured stream fixtures for each bundled harness; argv
  substitution (closed vars, empty-drop, no shell); schema valid/invalid adapters; `DeclarativeExecutor.run`
  over a scripted stream (no real binary); registry loads a workspace adapter and resolves `kind`.
- **Integration:** the harness node driving a `DeclarativeExecutor` end-to-end (sandbox lifecycle,
  budget kill, `exec.result`, trace step) — the P2 harness-node tests, re-pointed at a declarative
  adapter, must stay green.
- **Demo:** a topology whose `coding-worker` archetype sets `executor: {kind: claude-code}` (now
  data) produces a diff artifact in a worktree with per-node cost; adding `kind: codex` is a
  one-line archetype change plus a bundled YAML, no rebuild.
