---
status: done
---

# Executor P2 — implementation plan (harness executor, end to end)

> **Status: COMPLETE.** All 8 PRs merged (runtime 1.61.0 → 1.69.0):
> #527 ExecEvent contract · #528 compiler dispatch · #529 worktree sandbox ·
> #530 budget/liveness · #531 claude-code adapter · #532 harness node execution ·
> #533 observability · #534 cockpit display. A `harness` node runs sandboxed, governed,
> budgeted, deny/abort-safe, and is observed (cost/trace/audit) identically to a `model`
> node. P3 (relay + input-escalation, container sandbox + egress proxy, `codex` adapter,
> Tier-2 declarative adapters, resume-with-feedback) is not started.

Decomposes RFC §12 **P2** (`executor-abstraction.md`) into codebase-grounded PRs. P1 (schema →
registry → resolution threading) is done: every `ResolvedAgent` carries a `ResolvedExecutor`, all
`model` today. P2 makes `harness` real — the `claude-code` adapter, worktree sandbox, budget
envelope, normalized events, observability from day one — with **interaction limited to
`deny`/`abort`** (the never-hang guarantee). `relay`/input-escalation are P3.

## The integration point (found)

The per-agent execution entry is `langgraph_compiler/_compiler.py::_build_agent_node` → the inner
`node_fn` (added via `graph.add_node(agent.id, node_fn)`). **This is where P2 dispatches on
`agent.executor.kind`.**

## The one pragmatic decision up front

**`model` keeps its current `node_fn`; only `harness` routes through the new `Executor.run()` →
`ExecEvent` path.** We do *not* refactor the model tool-loop through `Executor.run()` in P2. Why:
- The tool-loop rewrite is large and risky, and buys nothing for P2 (there's no second consumer of a
  normalized model event stream yet — model's observability is already the OTel spans + usage/cost
  recording shipped in the monitor work).
- So the compiler branch is: `if kind == "model": <today's node_fn, unchanged>; elif kind ==
  "harness": <harness runner>`. Model behavior is byte-identical.
- A later refactor *may* route `model` through `ModelExecutor.run()` for a uniform event stream — but
  that's an optional cleanup, not a P2 dependency.

## PR sequence (dependency-ordered)

1. **`ExecEvent` vocabulary + `Executor.run()` contract** — the normalized event dataclasses (§5.1:
   `exec.started/message/tool_call/artifact/usage/approval_requested/input_requested/result/raw`) and
   the async `run(task, sandbox, budget) -> AsyncIterator[ExecEvent]` + `preflight` on the `Executor`
   ABC. `ModelExecutor.run` stays `NotImplementedError` (P2 wires only harness through it). Pure
   additive; unit-tested against the event shapes.
2. **Compiler dispatch on `executor.kind`** — in `_build_agent_node`, branch to the current model
   `node_fn` for `model`, and to a `harness` runner for `harness`. For this PR the harness branch is a
   guarded stub (`ExecutorError: harness execution not yet available`) so the seam lands and every
   model run is unchanged. Regression tests: existing topologies compile + run identically.
3. **Worktree sandbox** — a `sandbox/` module: provision a git worktree from a base ref, hand back a
   `SandboxHandle`, tear down on exit (incl. failure). `network: deny` in P2 = grant no network tools
   / no egress config; the **egress proxy + container sandbox are deferred** (their own hard piece,
   P3+). Ownership rule enforced: the executor node produces a diff, never integrates it.
4. **Budget envelope + liveness** — core-owned enforcement consuming `exec.usage`: hard-kill on
   `max_cost_usd` / `max_turns` / `max_wall_clock_minutes`, and an **idle-timeout** (`max_idle_seconds`
   — no event in the window ⇒ probe then kill with `exec.result{status: stalled}`). Semantic status
   over exit codes (typed output + artifact-manifest match).
5. **`claude-code` adapter (Tier 1)** — launch `claude -p --output-format stream-json --verbose`,
   translate its message/tool-call/status events → `ExecEvent`s, map `--max-budget-usd`/`--max-turns`
   → the envelope, `--json-schema` → `output_schema`, `session_id` → resume token. Preflight (binary
   present, version, credential resolvable). Reference adapter proving the contract.
6. **Harness node execution** — wire the harness branch (from PR 2): preflight → provision sandbox →
   inject `TaskSpec` (§6.0: task statement, workspace context file as `CLAUDE.md`, granted MCP tools,
   base ref) → `run()` streaming `ExecEvent`s → enforce budget/idle → collect the result artifact →
   teardown → pass to the next node/gate. Interaction: **`deny`/`abort` only** (§6.2) — a request
   outside the grant refuses in place or terminates with `exec.result{status: needs_approval}`; never
   hangs. Checkpoint before launch + at `exec.result`.
7. **Observability from day one** — map `ExecEvent`s onto what's already shipped, no new backend
   design: `exec.*` → an OTel `executor` span nested under the node/topology spans (extends the trace
   tree in `/observability/runs/{id}/trace`); governance-relevant events → the `/audit` projection;
   `exec.usage` → the usage/cost recording (vendor `cost_usd` authoritative, else the price table —
   the exact fallback already implemented). `executor.kind/ref/model` span attributes so `model` and
   `harness` nodes query uniformly.
8. **Cockpit display** — surface harness `ExecEvent`s in the run-detail monitor (the trace waterfall
   already renders the span tree; add the executor span + result). The live topology canvas (its own
   feature) becomes the richer seat later.

## Governance (AGT), every PR that touches it

Node-visible delegation (archetype, capability set, budget) is auditable; topology-approval sees a
harness node as a distinct reviewable fact; the diff never merges on the harness's say-so — it faces
the declared downstream gates. Two-class credentials: the **model-provider** key is env-injected
per-run + scrubbed; all other secrets are **not** in the executor environment (proxy-injected only —
and the proxy is P3, so P2 harness nodes get *no* non-model secrets).

## Explicitly deferred to P3+ (not in P2)

`relay` + input-escalation + the question classifier (§6.2–6.3); trust-accrual → allowlist changeset;
resume-token retry-with-feedback; container sandbox + egress proxy + network allowlists; the `codex`
adapter (cross-vendor proof); Tier-2 declarative `adapter.yaml`. P2 is one adapter, worktree, deny/
abort, observed.

## Acceptance (subset of RFC §10 relevant to P2)

- Existing workspaces run unmodified (`model` path byte-identical).
- A demo topology routes one slice to `claude-code`; per-node cost is visible from `exec.usage`.
- Budget breach hard-terminates with `exec.result{status: budget_exceeded}` → failure edge.
- An out-of-grant request under `deny`/`abort` never hangs (regression against the headless-hang mode).
- A stalled subprocess is killed at the idle timeout; exit-0-without-matching-artifact = failure.
- One OTel `executor` span per harness node with the standard attributes; audit records the delegation.

## Test / demo plan

- Unit: `ExecEvent` shapes; budget/idle enforcement (mock clock + a fake event stream); the
  claude-code event→`ExecEvent` mapping (fixture of real stream-json lines); sandbox provision/teardown
  (tmp git repo).
- Integration: the harness branch with a **mock adapter** (no real `claude` binary) emitting a scripted
  stream → asserts sandbox lifecycle, budget kill, `exec.result`, and the executor span.
- Demo: a topology with a `coding-worker` (kind: harness, mock or real claude-code) produces a diff
  artifact in a worktree with per-node cost on the run-detail page; a budgeted breach terminates cleanly.
