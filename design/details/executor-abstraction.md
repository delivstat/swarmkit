---
status: draft
---

# Feature Request: Pluggable Executor Abstraction at the Archetype Level

**Type:** Feature / Architecture RFC
**Component:** Archetype schema, runtime (LangGraph node execution), AGT governance, cost accounting, observability (OTel/audit)
**Status:** Proposed — all design questions resolved (§11); ready for implementation review
**Priority:** High — converts SwarmKit's largest quality gap (raw model calls vs. engineered coding harnesses) into a pass-through

---

## 0. Architectural placement — a recognized provider seam (invariant note)

This RFC introduces `executor`, a new extension mechanism (adapters), which sits against CLAUDE.md
**invariant #2 — "Skills are the only extension primitive."** That boundary is drawn deliberately here,
not left to drift.

**Resolution: `executor` is a node-execution *provider seam*, not a capability primitive.** It is the
same class of abstraction as `ModelProvider` (invariant #4 — "all LLM calls go through `ModelProvider`"),
`GovernanceProvider` (invariant #3), and the audit provider: a narrow, swapped-at-startup interface the
runtime depends on. `model` becomes *one* executor kind and `harness` another (§4.2, P1) — so this
**generalizes the existing `ModelProvider` seam** rather than adding a parallel one. An executor answers
*how a node does its work*; a skill answers *what capability an agent may invoke*. Different layers.

The skill/executor line stays crisp by construction (§9): a bounded, stateless consult that answers and
returns is tool-shaped → an MCP skill; a blackbox that *pursues* (multi-turn, holds a session, produces
a diff) → an executor. **If it produces a diff or holds a session, it is an executor; if it answers a
question and returns, it may be a tool.**

**Action on acceptance:** promote `executor` to a listed provider abstraction alongside
Model / Governance / Audit (design §7 principles, §9), and amend invariant #2 to read *"Skills are the
only **capability** extension primitive; node execution is the Model/executor seam."* Do this
explicitly — as §15.3's UI-deferral was consciously revisited — so the invariant and this feature never
read as contradictory.

**Cockpit:** the live topology canvas (`topology-canvas.md`) is the visual seat for this feature's
mid-run interaction (§6.2–6.3) — `exec.approval_requested` / `exec.input_requested` surface on the graph
node and are answered inline. The two features are designed together.

---

## 1. Summary

Introduce a first-class `executor` block in the archetype schema that declares *how* a node's work is performed, decoupled from *what* the node does. The initial implementation ships two executor kinds:

- `model` — the current behavior: a direct chat-completion call to a configured model (OpenRouter or otherwise). Remains the default; existing archetypes are unaffected.
- `harness` — delegation of the node's task to an external agentic harness running as a sandboxed subprocess, communicating over a normalized JSONL event contract. Coding harnesses (Claude Code, Codex) are the first targets, but the contract is domain-agnostic: any goal-pursuing external agent — audio production, route planning, document assembly, whatever ships next — plugs in through the same interface. The core contract is **task spec in → normalized events during → typed artifacts out**; "a diff in a git worktree" is one artifact profile, not the definition.

The executor interface must be defined as an open plugin contract so that future executor kinds (new harnesses, remote agent services, or executor types that do not exist yet) can be added by implementing an adapter — with **no changes to the archetype schema, the runtime, governance, or the cockpit**. Adapters come in two tiers (§5.2): shipped **code adapters** for complex integrations, and **declarative adapter definitions** — YAML workspace artifacts — for the common subprocess-plus-JSONL shape, so new harnesses can be integrated without a core release.

## 2. Motivation

SwarmKit workers currently execute via raw model calls inside LangGraph nodes. For real coding tasks, this competes with tools like Claude Code and Codex — which are not merely models but years of harness engineering (agentic edit–verify–retry loops, codebase-scale context management, file-editing reliability, test-running reflexes, error recovery). A raw frontier model in a chat loop does not match a mid-tier model inside a mature harness for this class of work.

Rebuilding that scaffolding inside SwarmKit is a multi-year mistake. The correct move is to let a leaf node *delegate* to a harness while SwarmKit retains what it is actually for: declared topology, structural gates, durable state, cost tiering, and the compounding artifact workspace.

Secondary motivations:

- **Per-slice routing.** The planner/topology can declare, per node, whether a slice warrants a harness (rate-limiter core → Claude Code) or a cheap model (config change → small OpenRouter model). This decision becomes declared, diffable, and reviewable rather than buried in prompts.
- **Ecosystem velocity.** The harness landscape is moving fast (Claude Code, Codex, Gemini CLI, OpenCode, Pi, Aider, and whatever ships next quarter). SwarmKit must be able to adopt a new harness by writing one adapter, not by redesigning the runtime.
- **Governance honesty.** Harness delegation must be *visible in the topology* so AGT can gate, budget, and audit it as a node — not smuggled through worker prompts or tool calls.

## 3. Non-Goals

- Reimplementing agentic coding loops inside SwarmKit.
- Modeling long-running harness delegation as an MCP tool available to arbitrary archetypes. (Delegation is an executor concern; see §8 for the narrow tool-shaped carve-out.)
- In-process/SDK embedding of vendor harnesses in v1. The initial contract is subprocess + JSONL; SDK embedding (e.g., Claude Agent SDK `can_use_tool` interception) is a possible v2 enhancement behind the same interface.
- Governing the harness's internal reasoning. Governance is applied at the boundary (sandbox, capability grants, budget, output gates), not inside the loop.

## 4. Proposed Schema Change

### 4.1 Archetype `executor` block

```yaml
# archetype: coding-worker
name: coding-worker
role: >
  Implements a bounded code change described by a task spec,
  in an isolated worktree, and returns a diff.
executor:
  kind: harness                    # enum: model | harness | <plugin-registered kinds>
  ref: claude-code                 # adapter id from the executor registry
  version_constraint: ">=2.1"      # optional; adapter interprets
  config:                          # opaque to core; validated by the adapter's own schema
    permission_mode: bare
    allowed_tools: [Read, Edit, Bash, Grep]
    output_schema: schemas/diff-result.json
  sandbox:
    type: worktree                 # worktree | container | tempdir | none
    network: deny                  # deny | allowlist | full
    credentials: proxy-injected    # non-model secrets never in env (see §7 two-class rule;
                                   # the model-provider credential alone is env-injected per-run)
  artifacts:
    profile: diff                  # diff | files | structured — what the node is expected to emit
    schema: schemas/diff-result.json   # required for structured; optional otherwise
  budget:
    max_cost_usd: 5.00
    max_turns: 40
    max_wall_clock_minutes: 30
  interaction:
    on_unanswerable: relay           # deny | abort | relay — what happens when the harness
                                     # requests something outside its grants (see §6.2)
    input_escalation: [lead, operator]   # who answers exec.input_requested, in order (see §6.3)
    human_required_patterns:         # question classes that always skip model escalation
      - naming
      - external-facing
  telemetry:
    stream: true                   # emit normalized events live
    retain_raw: true               # keep the raw vendor event log alongside normalized events
```

Note on `config.allowed_tools`: the tool list is a **closed set with deny-all-else semantics by construction** — an invariant, not an option. Anything not listed is denied at launch (mapped to the vendor's pre-approval mechanism, e.g. `--allowedTools` / sandbox level). Runtime relaying (§6.1) never widens the effective grant for the current run beyond a single approved action; permanent widening happens only by amending this list through a reviewed changeset.

```yaml
# archetype: analysis-worker (unchanged behavior, now explicit)
name: analysis-worker
executor:
  kind: model
  ref: openrouter/deepseek-v4
  config:
    temperature: 0.2
```

### 4.2 Rules

1. `executor` is **optional**; absence means `kind: model` with the archetype's existing model configuration. Full backward compatibility — no existing workspace artifact changes meaning.
2. `executor.kind` values are **not a closed enum in core**. Core validates that `kind` matches a registered executor plugin; the plugin supplies the JSON Schema used to validate its own `config` block. Adding a new executor kind must require zero core schema changes.
3. `executor.config` is opaque to core and owned by the adapter. Core owns and enforces `sandbox`, `budget`, and `telemetry` uniformly across all kinds.
4. A topology may override archetype executor fields per node instance (e.g., tighten a budget), but may not change `kind` — swapping delegation semantics is an archetype-level decision and must be reviewed as one.

## 5. Executor Plugin Interface (the extensibility contract)

Each executor is an adapter implementing:

```python
class Executor(Protocol):
    kind: str                      # registry key, e.g. "harness"
    ref: str                       # adapter id, e.g. "claude-code"

    def config_schema(self) -> JSONSchema: ...
    def preflight(self, node_ctx) -> PreflightReport:
        """Binary present? version ok? credentials resolvable via proxy?
        Sandbox provisionable? Fail fast before any spend."""

    async def run(self, task: TaskSpec, env: SandboxHandle,
                  budget: BudgetEnvelope) -> AsyncIterator[ExecEvent]:
        """Launch, translate the vendor's native event stream into
        normalized ExecEvents, enforce nothing itself — budget/sandbox
        enforcement hooks are supplied by core."""

    async def cancel(self, run_id) -> None: ...
    def resume_token(self, run_id) -> ResumeToken | None:
        """Vendor session id if the harness supports resume; enables
        checkpoint/restore across AGT gates and retry loops."""
```

### 5.1 Normalized event schema (`ExecEvent`)

All adapters translate their vendor's native stream into this vocabulary. The cockpit, cost meter, AGT audit log, and checkpoint store consume **only** these:

| Event | Payload (minimum) |
|---|---|
| `exec.started` | run_id, executor kind/ref, resolved config hash |
| `exec.message` | role, text (assistant/user/system messages, thought summaries where the vendor exposes them) |
| `exec.tool_call` | tool name, input summary, status |
| `exec.artifact` | artifact kind (file_change, media, structured), path or ref, mime/type metadata |
| `exec.usage` | units-typed consumption: tokens (input/output/cached/reasoning), or vendor-native units (characters, credits, requests) with a declared unit; cost_usd (nullable) |
| `exec.approval_requested` | requested capability (tool/pattern), harness rationale if available, run_id — a *permission* question: "may I?" (see §6.2) |
| `exec.approval_response` | granted/denied, responder (policy \| operator), scope (this-action-only) |
| `exec.input_requested` | question text, structured options[] with harness trade-off notes where available, free_text_allowed, question class — a *judgment* question: "what do you want?" (see §6.3) |
| `exec.input_response` | answer, responder (lead \| operator \| memoized), injected-at timestamp |
| `exec.result` | status (success/failure/budget_exceeded/cancelled/needs_approval/stalled), typed output (per `output_schema` if set), artifact manifest matching the declared `artifacts.profile`, exit metadata |
| `exec.raw` | passthrough of the untranslated vendor line (retained when `telemetry.retain_raw`) |

Reference mappings, to prove the schema against real vendors:

- **Claude Code**: `claude -p --output-format stream-json --verbose` → events per message/tool call/status; final result carries `cost_usd`, `session_id`, and full token usage. `--max-budget-usd` / `--max-turns` map to the budget envelope natively; `--json-schema` maps to `output_schema`; session id maps to `resume_token`.
- **Codex**: `codex exec --json` JSONL → `thread.started`, `turn.*`, `item.*` (commands, file changes, MCP calls, reasoning items); `turn.completed.usage` includes `reasoning_output_tokens`; `codex exec resume <session>` maps to `resume_token`; `--output-schema` maps to `output_schema`; sandbox levels map to the sandbox contract.

An adapter for a harness that emits **no** structured telemetry may still be written, but must declare `telemetry_grade: opaque`; core then requires an explicit topology-level acknowledgment to use it, and the cockpit displays it as unobservable. (Policy: SwarmKit prefers harnesses that narrate their pursuit in JSON.)

### 5.2 Two-tier adapter model

Every harness speaks its own event dialect (Claude Code: message/tool-call/status events; Codex: `thread.*`/`turn.*`/`item.*`). Adapters therefore come in two tiers:

**Tier 1 — code adapters (shipped, trusted).** Reference adapters (`claude-code`, `codex`) ship with SwarmKit; third-party code adapters install via a plugin entry-point and are human-vetted. Code adapters handle the hard cases: resume tokens, stateful stderr parsing, non-JSONL streams, vendor-specific budget flags.

**Tier 2 — declarative adapter definitions (workspace artifacts).** Most CLI harnesses share one shape: spawn a subprocess from a command template, read line-delimited JSON, map fields into `ExecEvent`s. That is configuration, not code. A workspace may therefore contain an `executor-adapter` artifact:

```yaml
kind: executor-adapter
name: gemini-cli
domain: coding                       # informational; any string (coding, audio, geo, docs, ...)
launch:
  command: ["gemini", "-p", "{task}", "--output-format", "jsonl"]
  workdir: "{sandbox.root}"
  env_passthrough: []                # explicit allowlist; empty by default
stream:
  format: jsonl                      # jsonl | json | lines
event_map:
  - match: {type: "tool_use"}
    emit: exec.tool_call
    fields: {tool: "$.name", input: "$.input"}
  - match: {type: "usage"}
    emit: exec.usage
    fields: {input_tokens: "$.in", output_tokens: "$.out", unit: "tokens"}
  - match: {type: "output_file"}
    emit: exec.artifact
    fields: {kind: "file", path: "$.path"}
result:
  success_when: {exit_code: 0}
  output: "$.final_message"
  artifacts_from: "{sandbox.root}/out"   # manifest scan, per artifacts.profile
```

Because a declarative adapter is data, it inherits the full workspace lifecycle: schema validation, pending-review, AGT gating, changesets, versioning — and it is inspectable: a reviewer sees exactly what command runs and what is extracted, without reading code. This is also the community scaling path: when a new harness ships (an audio-production harness, a route-planning harness), the integration is a shareable `adapter.yaml`, not a core PR.

Tier-2 constraints, by construction:

1. **The launch template is the sharpest edge** (it is a command line). Declarative adapters carry a mandatory human-review gate on first approval and on any change to `launch`, regardless of the workspace's auto-run trust settings. Command arrays are templated with a closed set of substitution variables (`{task}`, `{sandbox.root}`, budget fields); no shell interpolation.
2. **No event invention.** The `event_map` can only translate what the vendor emits. A harness with no structured output can only ever yield a `telemetry_grade: opaque` adapter — honest by construction.
3. **Graduation path.** A declarative adapter that hits its ceiling (resume logic, bidirectional streaming, non-line-oriented output) declares `requires: code` and graduates to Tier 1. In particular, **mid-run interaction (§6.2 relay, §6.3 input requests) is Tier-1-only**: it requires bidirectional session control and question-detection heuristics. Tier-2 adapters are limited to `on_unanswerable: deny | abort`.
3a. **Exit codes are necessary, not sufficient.** `success_when: {exit_code: 0}` alone is naive — a harness can exit 0 having refused or under-delivered. Core layers the semantic check (§6.1: typed output present, artifact manifest matches `artifacts.profile`) on top of any Tier-2 `success_when`, for all adapters.
4. **Executor vs. tool discipline still applies.** A one-shot request/response service (plain text-to-speech call, single geocode) is tool-shaped and belongs in an MCP skill, not an executor adapter. Executor adapters are for blackboxes that *pursue* — multi-turn, goal-directed, self-checking runs — in any domain.

## 6. Runtime Integration

### 6.0 Task spec and context injection (what goes IN)

The `TaskSpec` handed to `run()` is a first-class, checkpointed artifact — not a bare prompt string. It comprises:

- **Task statement** — the slice, its acceptance criteria, and explicit instructions to proceed on reasonable assumptions rather than ask (the headless prompt discipline).
- **Pre-answered decisions** — memoized `exec.input_response` answers from prior rounds (§6.2) and any decisions the plan already made, injected so the harness never re-asks.
- **Workspace context file** — the adapter materializes workspace conventions into the harness's native context mechanism (`CLAUDE.md` for Claude Code, `AGENTS.md` for Codex/OpenCode) inside the sandbox. This is the bridge between workspace skills/conventions and the harness's own context system, generated per-run from declared sources — never inherited from whatever is on the host.
- **Tool mounting** — workspace MCP skills granted to the archetype may be mounted into the harness via its native MCP configuration; the harness's `allowed_tools` grant and the MCP mount list are the same reviewed capability surface, expressed in two vendor mechanisms.
- **Base state** — for `worktree` sandboxes: the base ref/branch the worktree is created from. **Ownership rule:** the executor node produces artifacts (a diff) but never integrates them; applying/merging a diff is the job of a downstream integrator node, after gates. Parallel harness nodes therefore never contend on the working tree.
- **Isolation from host config** — harnesses are launched in their vendor's clean/deterministic mode (e.g., `--bare`, `--ignore-user-config`-style flags) so runs are reproducible and do not absorb the host user's personal settings, hooks, or credentials.

### 6.1 Node lifecycle

- A node whose archetype declares `kind: harness` compiles to a LangGraph node that: provisions the sandbox (worktree/container), runs `preflight`, launches the adapter, streams `ExecEvent`s into graph state and the event bus, enforces the budget envelope (hard-kill on breach), collects the result artifact (diff + logs), tears down the sandbox, and passes the result to the next node/gate.
- **Checkpointing:** the node checkpoints (a) before launch and (b) at `exec.result`. If the adapter supplies a `resume_token`, mid-run interruption (AGT gate, crash, host restart) may resume the vendor session; otherwise the run restarts from the task spec. Long-running harness work must not break graph durability.
- **Liveness, distinct from wall-clock:** `max_wall_clock_minutes` bounds total duration, but a hung subprocess emitting no events is a separate failure mode. The runtime enforces an idle timeout (`max_idle_seconds`, default sane) — no `ExecEvent` within the window ⇒ probe, then kill with `exec.result{status: stalled}`. A harness must narrate or die.
- **Retry-with-feedback:** when a downstream gate rejects the artifact (reviewer comments, failed tests), the retry prefers `resume_token` continuation — feedback injected into the *same* vendor session, preserving the harness's context of its own work — falling back to a fresh run with feedback appended to the task spec when no resume is available or the session is poisoned. Retry count and per-retry budget are part of the envelope, not unbounded.
- **Semantic status over exit codes:** a harness can exit 0 having *refused* or silently under-delivered — exit codes cannot express refusal. The node derives success from the structured result (typed output present, artifact manifest matches the declared `artifacts.profile`), never from the exit code alone. An empty diff where a diff was declared is a failure, not a success.
- **Vendor session residue:** harnesses persist transcripts/session files on disk by default (vendor session stores, rollout files). The adapter contract requires runs to be ephemeral where the vendor supports it, or explicit cleanup of session residue at teardown otherwise; `exec.raw` retention in SwarmKit's own audit store is the sanctioned copy. This keeps run data residency in the workspace, not scattered across host dotfiles.
- **Concurrency:** harness nodes are expensive; the runtime honors a per-topology and per-workspace concurrency cap for `kind: harness` nodes, separate from model-node concurrency.

### 6.2 Mid-run permission requests ("may I?")

In a headless pipe, an unanswered interactive prompt is a hang (the known failure mode across harnesses). The design therefore treats permissions in three layers:

1. **Pre-answered at design time (the baseline).** The archetype's closed `allowed_tools` set and sandbox level are mapped to the vendor's pre-approval mechanism at launch. Most permission questions should never occur at runtime because the reviewed archetype already answered them.
2. **`on_unanswerable` policy** governs anything outside the grant:
   - `deny` — the action is refused in-place; the harness continues or fails on its own logic. Default for mature archetypes.
   - `abort` — the run terminates with `exec.result{status: needs_approval}`; budget released; graph takes the failure edge. Safe default for Tier-2 adapters and untrusted archetypes.
   - `relay` — the adapter emits `exec.approval_requested`; the node **interrupts and checkpoints** (session held open or parked via resume token); the request lands in the cockpit approval inbox as an AGT gate; on response, `exec.approval_response` is fed back (SDK permission callback or session resume) and the run continues. An approval is scoped to that single action — it does not widen the run's grant.
3. **Trust accrual → changeset promotion.** Every relayed approval is recorded against the `(archetype, capability-pattern)` pair. When approvals for a pattern cross a threshold with no denials, the system does not silently widen anything — it **proposes a changeset** amending the archetype's `allowed_tools` ("coding-worker requested `Bash(npm test)` 7 times, approved every time — add to allowlist?"). The operator approves; future runs never ask. Runtime relaying is the evidence-gathering phase; the allowlist is where trust is permanently and reviewably recorded. A mature archetype then flips to `on_unanswerable: deny`.

### 6.3 Mid-run input requests ("what do you want?")

Distinct from permissions: an input request is a **domain-judgment question** — e.g., the harness identifies three viable implementations for a new endpoint and asks which to use, or needs a name for a new config key. These are routed as work, not as policy:

1. The adapter (Tier 1) detects the question and emits `exec.input_requested` with a structured payload: question text, enumerated options with the harness's trade-off notes where available, and whether free text is acceptable. **Detection is a shared core classifier, not per-adapter regex** (decision 7): cheap pre-filters (final turn took no action / expected artifact absent) gate a small structured-output LLM call that identifies the question and extracts options — language-agnostic by construction, since harnesses respond in the task's language and punctuation heuristics fail outside English. Misfires are possible and resolve harmlessly as a no-op answer or operator dismiss; adapters prefer a native vendor question-event where one exists.
2. The node interrupts and checkpoints; the question becomes durable graph state (answerable days later, like any gate).
3. The question routes up the archetype's `input_escalation` chain. First stop is typically the worker's **lead node** — a capable model holding the topology context, approved design, and workspace conventions — which answers within a small token budget when the approved design already implies the answer (implementation-choice questions often qualify). The answer is injected into the harness session (streaming input / session resume) and the run continues without human interruption.
4. Questions matching `human_required_patterns` (e.g., naming of external-facing things), or declined by the lead, land in the cockpit inbox as a human gate.
5. Every question–answer pair is recorded (`exec.input_response`) into graph state and the run record, enabling:
   - **Memoization:** on topology re-runs and retry loops, previously answered questions are pre-injected into the task spec so the harness never asks twice.
   - **Spec-quality feedback:** recurring question classes are mined across runs as a signal of missing workspace conventions — the durable fix for repeated config-naming questions is a naming-conventions skill or a planner that pre-answers the decision in the task spec, not faster answering. Questions are the workspace revealing what it hasn't yet learned.

## 7. Governance (AGT) Integration

- Delegation to a harness is a **node-visible act**: AGT sees "archetype X delegated task Y to harness Z with capability set C and budget B," not a generic tool call.
- Gateable points: (1) topology approval — a topology introducing a harness node with elevated sandbox/network is a distinct reviewable fact; (2) pre-launch — optional AGT gate before any harness run in sensitive workspaces; (3) post-result — the diff never merges on the harness's say-so; it passes the declared downstream gates (review node, tests, human approval).
- Credentials follow a two-class rule: the **model-provider credential** (the one exception — the harness must reach its LLM API) is runtime-injected into the launch environment from the workspace credential store, scoped per-run and scrubbed from all persistence; **all other secrets** (VCS tokens, cloud credentials) are never placed in the executor's environment and are injected only at the egress proxy on approved requests.
- **Nested delegation is inside the boundary.** Harnesses spawn their own internal subagents (Claude Code subagents, Codex sub-tasks); these are invisible to the topology by design and governed only through the boundary: they inherit the node's sandbox, count against the node's budget envelope, and their effects surface only in the node's artifacts. No topology-level visibility inside the harness is promised or attempted.
- **Untrusted content flows through the harness.** A coding harness reads repository files, which may contain adversarial instructions (prompt injection via source/comments/docs). The defense is the same boundary model, stated explicitly: injected instructions cannot exceed the sandbox, the capability grant, the network policy, or the budget — and the artifact still faces every downstream gate. A compromised run wastes an envelope; it does not gain reach.
- All `ExecEvent`s land in the AGT audit log; `exec.raw` retention gives a forensic trail without making the normalized layer vendor-specific.

## 8. Observability: Tracing, Logging, and Audit

Three distinct consumers read executor activity, and they must not be conflated: **audit** (governance/compliance — did approvals happen, who answered, what merged), **tracing** (performance/debugging — where did latency and spend go), and **logging** (raw diagnostics — what did the harness actually emit). The `ExecEvent` stream is the single source; each consumer projects from it.

### 8.1 OpenTelemetry trace model

A harness run maps naturally onto OTel spans, and the key requirement is **context propagation across the process boundary** so a harness run is not an opaque leaf in the trace.

- **Span hierarchy:** the topology run is the root span; each node is a child span; a `kind: harness` node opens an `executor` span at launch and closes it at `exec.result`. Within it, the adapter opens child spans per meaningful unit derived from the event stream — `exec.tool_call` → a tool span, `exec.approval_requested`→`response` → a gate span (whose duration legitimately includes days of human wait, flagged so it doesn't pollute latency percentiles), `exec.input_requested`→`response` → an escalation span.
- **Cross-boundary propagation:** the launch injects W3C trace context (`traceparent`) into the harness environment so that harnesses which emit their own OTel (or whose events carry correlation ids) nest under the SwarmKit span rather than starting a detached trace. Where the harness emits nothing traceable, the adapter synthesizes child spans from the normalized events — so the trace is complete regardless of vendor instrumentation maturity.
- **Nested subagents (§7):** a harness's internal subagents are *not* promised as individual spans (they're inside the boundary). They appear only insofar as they surface as `exec.*` events. This is stated so trace gaps under a harness span are understood as by-design, not missing instrumentation.

### 8.2 Standard span attributes

Every executor span carries a normalized attribute set so dashboards work identically across vendors and across `model` vs. `harness` kinds. Following OTel GenAI semantic conventions where they exist, plus SwarmKit-specific keys:

- `swarmkit.workspace`, `swarmkit.topology`, `swarmkit.run_id`, `swarmkit.node_id`, `swarmkit.archetype`
- `executor.kind` (`model`|`harness`), `executor.ref` (`claude-code`), `executor.model` (per open question 9 — the field that makes model-level trace queries work across both kinds)
- `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, plus SwarmKit's unit-typed extension for non-token harnesses (`executor.usage.unit`, `executor.usage.amount`) so an audio/geo harness meters in its own units
- `executor.cost_usd`, `executor.result.status`, `executor.sandbox.type`, `executor.budget.max_cost_usd`

Cardinality discipline: task text, file contents, diffs, and full messages are **not** span attributes — they are span *events* or log records referenced by id, keeping the trace backend lean.

### 8.3 Audit log (the governance projection)

The audit log is a separate, tamper-evident, retained projection — not the trace backend, which is sampled and short-lived. It records only governance-relevant facts, each stamped with responder identity and timestamp: harness delegation (archetype, capability set, budget), every `approval_requested`/`response` with responder, every `input_requested`/`response` with responder (`lead`|`operator`|`memoized`), gate outcomes, artifact acceptance/merge, and trust-accrual changeset proposals and approvals. This is the record that answers "prove this change was approved before it merged" — and it is exactly where a Rynko Flow attestation attaches, since it is the boundary-crossing record.

### 8.4 Log records and raw retention

- **Normalized log stream:** each `ExecEvent` is emitted as a structured log record (JSON), correlated to its span via trace/span id, at appropriate severity (`tool_call` info, `stalled`/`budget_exceeded` error). This is the queryable operational log.
- **Raw vendor retention:** `exec.raw` preserves the untranslated vendor JSONL, stored against the run (not the trace backend), retained per workspace policy. This is the forensic ground truth when a normalized event is ever disputed or an adapter mapping is suspected — the "tee the raw stream before parsing" discipline, made a contract.
- **Redaction:** logs and raw retention pass through the same egress/secret policy as the sandbox — proxy-injected credentials and secret patterns are scrubbed before persistence, so the observability layer never becomes the leak the sandbox prevented.

### 8.5 Configuration

An `observability` block (workspace-level default, archetype-override) declares: OTel exporter endpoint/headers, trace sample rate (audit log is **never** sampled — sampling applies to traces/logs only), raw-retention TTL, and redaction rule set. Absence ⇒ audit log on (required), traces/logs off — observability defaults safe, not silent on governance.

## 9. Related but Out-of-Scope Carve-Out: Harness-as-Oracle Tool

A bounded, stateless, synchronous consult ("review this 40-line snippet and return comments") is tool-shaped, not delegation-shaped. A separate `consult_harness` MCP skill MAY exist in the workspace, access-controlled per archetype like any other tool. The dividing rule, to be documented in both features: **if it produces a diff or holds a session, it is an executor; if it answers a question and returns, it may be a tool.** This feature request covers only the executor side.

## 10. Acceptance Criteria

1. Existing workspaces load and run unmodified (`executor` absent ⇒ current model behavior).
2. `claude-code` and `codex` adapters ship as reference implementations; a demo topology routes one slice to each and one to a small model, with per-node cost visible from `exec.usage` events.
3. A third adapter (suggested: `opencode` or `gemini-cli`) is implemented as a **Tier-2 declarative `adapter.yaml`** following a contributor-facing guide, without touching core — this is the proof of both the plugin contract and the declarative schema.
3a. A non-coding harness adapter (real or stubbed — e.g., a mock audio-production harness emitting `exec.artifact{kind: media}`) runs end-to-end with `artifacts.profile: files`, proving the contract is not coding-specific.
4. Budget breach on a harness node hard-terminates the run, emits `exec.result{status: budget_exceeded}`, and the graph proceeds to its failure edge.
5. A graph interrupted at an AGT gate mid-topology resumes days later; harness nodes with resume tokens continue their vendor session, others restart cleanly.
6. AGT audit log distinguishes harness delegation events from model calls and from tool calls.
7. Schema validation rejects: unknown `kind` with no registered plugin; `config` failing the adapter's schema; harness archetypes missing a `sandbox` block.
8. A harness action outside `allowed_tools` under `on_unanswerable: deny|abort` never hangs the node — the run refuses or terminates deterministically (regression test against the known headless-hang failure mode).
9. Under `on_unanswerable: relay`, an out-of-grant request interrupts the node, appears in the approval inbox, and — after a response delivered hours later — the run resumes and completes; the approval is scoped to the single action.
10. After N approvals of the same capability pattern, a changeset proposing the allowlist amendment is generated for operator review; no grant widens without it.
11. An `exec.input_requested` with enumerated options is answered by the lead node within its token budget and injected back without human involvement; a question matching `human_required_patterns` bypasses the lead and lands in the inbox. On topology re-run, the memoized answer is pre-injected and the question does not recur.
12. A harness subprocess that stops emitting events is killed at the idle timeout with `exec.result{status: stalled}`; a run exiting 0 with an artifact manifest that does not match the declared `artifacts.profile` is recorded as failure, not success.
13. A gate-rejected artifact triggers a retry that resumes the vendor session with reviewer feedback injected (where a resume token exists); retries respect their declared count and per-retry budget.
14. After teardown, no vendor session residue remains outside the SwarmKit audit store (ephemeral mode or verified cleanup); two parallel harness nodes on the same repo produce artifacts without working-tree contention, and integration occurs only in the downstream integrator node.
15. A harness run produces a single OTel `executor` span nested under its node and topology spans, carrying the standard attribute set (including `executor.kind`/`ref`/`model` and unit-typed usage); tool calls, approval gates, and input escalations appear as child spans, with human-wait duration flagged. `model` and `harness` nodes are queryable uniformly by `executor.model`.
16. The audit log records every approval/input response with responder identity and is never sampled; credentials/secrets are scrubbed from logs and `exec.raw` before persistence. Disabling trace export does not disable the audit log.

## 11. Resolved Decisions (formerly Open Questions)

1. **Adapter distribution — decided (§5.2):** two tiers — core-bundled/pip-installed code adapters (Tier 1) and declarative `adapter.yaml` workspace artifacts (Tier 2) with mandatory human review on the `launch` block.
1a. **Declarative DSL ceiling — decided:** v1 interpreter is deliberately minimal — line-delimited JSON streams only, literal equality matching on event fields, JSONPath field extraction. No regex, conditionals, or multi-line aggregation. Anything beyond is `requires: code`. Expansion is demand-driven: recurring walls hit by real community adapter attempts define the next feature, not speculation. Rationale: every interpreter feature is maintained engine code and reviewer attack surface; the graduation path makes under-building cheap.
1b. **Community adapter registry — deferred:** no registry for now; adapters are shared as ordinary workspace artifacts. Revisit if organic demand appears; provenance/signing requirements to be defined then.
2. **Authoring-module authored adapters — decided:** the authoring swarm MAY generate Tier-2 declarative adapters into pending-review, on the same footing as topologies/archetypes/skills — every authored artifact requires human approval before use, and the `launch`-block human gate (§5.2) applies with no exemption. Tier-1 (code) adapters remain human-authored.
3. **Cost normalization — decided:** vendor-reported `cost_usd` is authoritative when present; tokens × price table is the fallback. The method used is recorded per run (`executor.cost.source: vendor | computed`) so the workspace meter is auditable.
4. **Auth for harness runs — decided:** both subscription and API-key modes supported; workspace-level default, archetype-level override. Mechanically these are **environment/auth-state**, not command flags: API keys are runtime-injected env vars (`ANTHROPIC_API_KEY`, `CODEX_API_KEY`) from the workspace credential store, scoped per-run and scrubbed from all persistence; subscription mode uses vendor auth state (long-lived setup tokens / saved CLI credentials) provisioned into the sandbox. Note: in headless mode an API-key env var takes precedence over subscription credentials where both exist — deterministic by design. Practical guidance encoded as a lint: high-concurrency topologies should override to API-key (subscriptions rate-limit parallel fleets). **Amendment to the credential rule (§4.1/§7):** the *model-provider* credential is the one exception to proxy-only injection — the harness must reach its LLM API — and is env-injected at launch; all other secrets (VCS tokens, cloud credentials) remain proxy-injected only.
5. **Telemetry-grade default — decided:** `telemetry_grade: opaque` adapters are **denied by default**; use requires explicit per-archetype opt-in. Unobservable execution is a deliberate choice, never a silent fallback.
6. **Trust-accrual threshold — decided:** default N=5 consistent approvals of a capability pattern triggers the allowlist changeset proposal; operator-tunable per workspace. A single denial resets the counter **and** blocks future proposals for that pattern until the operator manually clears the block — a denial is a signal, not noise.
7. **Question detection — decided:** shared implementation in core, and it is a **classifier, not a regex**: cheap pre-filters (final turn took no action / expected artifact absent) gate a small structured-output LLM call ("does this final message request input? extract question + options"). This is language-agnostic by construction — harnesses respond in the task's language, and punctuation heuristics fail outside English (か, ¿, unmarked-question languages). The classifier seat uses a small, cheap model per the tiered strategy. Adapters MUST prefer a native vendor question-event/callback where one exists.
8. **Lead-answer accountability — decided:** lead-node answers to `exec.input_requested` are surfaced in run review by default. Delegated judgment that cannot be audited is not governed.
9. **First-class `executor.model` — decided:** promoted to a core-recognized optional field; adapters map it to the vendor's model flag. Gives `model` and `harness` nodes a uniform, core-visible model attribute for cost queries and planner routing.

## 12. Phasing

- **P1:** schema + registry + `model` executor formalized behind the interface (pure refactor, no behavior change).
- **P2:** `claude-code` adapter, worktree sandbox, budget envelope, normalized events, cockpit display. Interaction: `deny`/`abort` only (never-hang guarantee). Observability: `executor` spans + audit log from day one (they consume the same event stream, so they are not deferrable).
- **P3:** `codex` adapter (proves normalization across vendors), resume-token checkpoint integration. Interaction: `relay` for permissions via approval inbox; `exec.input_requested` with lead-node escalation and memoization. Observability: cross-boundary trace-context propagation; gate/escalation spans.
- **P3.5:** trust-accrual → allowlist changeset proposals.
- **P4:** Tier-2 declarative adapter engine (`executor-adapter` artifact schema, event_map interpreter, launch-block review gate) + contributor guide; a contributor-built `adapter.yaml` proves the contract.
- **P5:** non-coding artifact profiles exercised end-to-end (files/structured/media); community adapter sharing evaluated (open question 1b).
