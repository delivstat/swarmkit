# SwarmKit architecture review — control plane + runtime (July 2026)

A senior-architect review of `packages/control-plane`, `packages/control-plane-ui`, and
`packages/runtime`. High-level architecture first, then low-level defects, then the
reusable-library extractions and a prioritized roadmap. Read-only analysis — no code was
changed producing this report.

Method: six independent deep-dives (panel Python; control-plane cross-cutting/data; fleet
UI; runtime providers; runtime core execution engine; runtime CLI/server) against a shared
rubric (below), cross-checked and consolidated here.

---

## 0. Review criteria (the rubric)

- **A. Architecture & modularity** — single-responsibility modules; layering (routes →
  service → persistence); a real composition root; explicit typed contracts at seams; no
  god-objects.
- **B. DRY / reuse** — no N-way copy-paste across siblings; recurring shapes become
  libraries, not patterns; one source of truth for types and policy tables.
- **C. Correctness & robustness** — async discipline (no blocking I/O on the loop);
  transaction/concurrency boundaries; consistent error taxonomy; input/resource bounds.
- **D. Security** — default-secure & least-privilege; secrets as references; middleware
  order & injection surface; fail-**closed** on control surfaces.
- **E. Frontend** — component/hook reuse; one data-fetching abstraction; type safety;
  a11y & Next conventions.
- **F. Delivery** — testability (DI seams, e2e happy path); operability (packaging,
  config, health, security gate).

## 0.1 Metrics baseline

| Signal | Measure |
|---|---|
| God-modules | `cli/__init__.py` **2323**, `server.py` **1422**, `_workspace_runtime.py` **938**, `_tool_loop.py` **810**, `_app.py` **796** |
| SQLite stores duplicated | **9** across both packages (runtime persistence/audit/telemetry/notifications + control-plane registry/aggregation/artifacts/proposals) |
| Verb→tier map copies | **4** (`connect._VERB_ROUTES`, `_verbs.VERB_TIERS`, `server._required_action`, UI `KNOWN_VERBS`) |
| UI tri-state duplication | ~**9** verbatim loading/error/empty ladders; **9** pages use `usePoll` |
| Suppressed complexity lints | multiple `# noqa: PLR0911/PLR0912/PLR0915` on the big closures |

---

## 1. Cross-package themes (the systemic findings)

These recur across *both* packages and matter more than any single line.

### T1 — God-modules + closure-registration + no service layer
`_app.py` (10 `_mount_*` closures), `server.py` (4 `_register_*` closures, `# noqa: PLR0915`),
and `cli/__init__.py` (~30 commands) all register routes/commands as nested closures with
**business logic inline in the handler**. There is no service layer, so: the panel's
approve/deploy orchestration, the server's artifact CRUD/reload, and the CLI's
observability aggregation can't be unit-tested or reused. The repo's own rule — "business
logic belongs in `WorkspaceRuntime`, not CLI functions" — is violated *in the CLI itself*
(`cli` reads `.swarmkit`, parses audit rows, runs raw SQL on `checkpoints.db`).
**Fix:** APIRouters / Typer sub-apps + a thin service layer per domain.

### T2 — Blocking synchronous I/O on the async event loop (both packages)
Every SQLite store is synchronous (`threading.Lock` + per-call `sqlite3`) yet called
directly from `async` handlers — control-plane (`_app.py` routes → 4 stores) and runtime
(`server.py` `execute_job`/`run_topology`/`auth_middleware` → `persistence/_sqlite.py`).
The runtime auth path also does a **synchronous, timeout-less JWKS fetch** on the loop
(`_jwt.py:67`), and `NotificationStore` shares one connection with **no lock**. The
control-plane's "WAL hardening" (#405) tuned the DB but the real bottleneck — a slow/locked
query freezing all concurrent requests on the loop thread — is untouched.
**Fix:** `await asyncio.to_thread(...)` (or `def` handlers for pure-DB routes) at the store
seam; timeout + threadpool the JWKS call.

### T3 — The verb→tier / serve-REST surface is quadruplicated
The command vocabulary and its authorization tiers live in **four** places across two
languages — `connect._VERB_ROUTES` (+`_TIER_RANK`), control-plane `_verbs.VERB_TIERS`
(+identical `_TIER_RANK`), `server._required_action` (the inverse map), and UI
`KNOWN_VERBS` — each with a "keep the two in sync" comment and **no test asserting they
agree**. The panel authorizes an enqueue against one copy; the connector re-validates
against another; the UI gates its form against a third. This is the highest-drift,
security-relevant surface in the system.
**Fix:** one canonical `VERB_ROUTES` table (shared module / schema package); everything
else derives from or is generated from it.

### T4 — The serve REST client is reimplemented per call site
`{"Authorization": f"Bearer …"}` + `base.rstrip("/")` + 401/403 handling + POST-then-poll
loops appear in panel `_connector.py` (4×, incl. the near-identical `run_authoring`/
`run_eval`), `_deploy.py`, and runtime `connect.py`. Two directions (panel→instance Mode A;
connector→loopback Mode B) hit the **same** serve routes with **different** hand-written
clients. Token resolution is also forked: `_connector.resolve_token` is a lesser copy of
the runtime's `resolve_secret_ref` (missing `credentials:` support) — a capability gap
disguised as a duplicate.
**Fix:** one typed `ServeClient` consumed by both directions; delete `resolve_token`.

### T5 — The SQLite store is copy-pasted 9× with inconsistent semantics
Nine stores repeat `connect + PRAGMA WAL + row_factory + json-blob columns + dataclass
rows`. Worse, they *disagree*: control-plane + runtime `persistence` use per-op connections
+ a `threading.Lock`; runtime `audit`/`notifications` use one long-lived
`check_same_thread=False` connection, one of them **with no lock at all**. Only
`SqliteRegistry` has a migration path — the other eight `CREATE TABLE IF NOT EXISTS` and
pray on the next schema change (a live upgrade hazard on the persistent `/data` volume).
The "sqlite now, Postgres later" seam is a docstring, not an abstraction (sqlite dialect —
`INSERT OR REPLACE`, `json_extract` rollups — is everywhere).
**Fix:** a shared `_SqliteStore` base (connection/WAL/row-mapping/migration-registry) that
also decides the locking + async-offload story once, behind a dialect seam.

### T6 — Hand-mirrored type/policy contracts, no codegen
Panel handlers return `dict[str, Any]`; the UI re-declares every shape in `types.ts` with
"Mirrors `public_dict()`" comments as the only enforcement; the verb map exists in three
languages. A field rename on the Python side compiles clean in TS and yields `undefined` at
runtime. The repo *already* has the fix pattern — `packages/schema` ships canonical JSON
Schema with dual Python+TS validators.
**Fix:** generate `types.ts` (and `KNOWN_VERBS`) from the panel's OpenAPI / the canonical
schema; have handlers return pydantic response models.

### T7 — Permissive-by-default on control surfaces (fail-open)
A pattern across the stack: the **panel serves fully open** with no tokens/OIDC (mint +
deploy + approve, guarded only by a stderr warning); the **runtime serve sets CORS `["*"]`
with `allow_credentials=True`, always** (the `serve` CLI never passes origins); **default
governance is `allow_all=True`** and `provider: custom` silently downgrades to it;
**decision-skill governance fails open** (bad JSON / missing skill → `verdict="pass"`); a
**`"*"` scope** bypasses the reserved-scope guard and grants god-mode to a transport token.
Individually defensible for dev; collectively the default posture leans insecure, contrary
to the design's "human-approval gates are structural."
**Fix:** default-secure at the app factory (not the CLI); fail-closed on required gates;
reject `"*"` in transport tokens.

### T8 — Concurrency correctness is claimed but not delivered
"Hardening for a real fleet's concurrency" is asserted, but: the compiler **corrupts
run-state across concurrent runs** (§2.C-CRIT); the serve **cross-contaminates concurrent
SSE chat streams** via a module-global listener list (confidentiality); the serve
concurrency gate is **soft** (429 effectively dead, unbounded job creation under burst);
cross-store approve/deploy is **non-atomic**. The stores' per-object locks are simultaneously
too coarse (serialize reads within a store, on the loop) and too narrow (don't coordinate
the four stores on the *same file*).

---

## 2. Severity-ranked findings by package

Severity: **Critical** (data loss / corruption / auth bypass), **High** (broken feature /
real security or correctness risk), **Medium**, **Low**.

### 2.A Runtime — core execution engine (`langgraph_compiler/`, `_workspace_runtime.py`)

- **CRITICAL — run-state shared across runs.** Task-plan/scope files live under a literal
  `.swarmkit/run-state/current/`, not namespaced by `run_id`/`thread_id`; the parent-agent
  pointer is a **module global** (`_compiler.py:49`), not a `ContextVar` (its sibling trace
  var deliberately *is* one). Two runs in one process (the stated `serve`/web-UI target)
  read/write each other's `tasks.json`/`scope.json`, and `_archive_run_state` `shutil.move`s
  `current/` out from under a still-running peer. Silent cross-run corruption; blocks serve.
- **HIGH — topology-as-data violation.** The runtime hardcodes an agent id
  `document-writer` as a synthesis role (`_task_plan.py:86,138`) and injects one customer's
  domain nouns — **"CDT/Jira"** — into the generic system prompt (`_prompts.py:206`,
  `_decision_gate.py:215`). Breaches invariant #1; leaks a tenant's domain to every swarm.
- **HIGH — divergent scope writers.** `_tool_loop._handle_create_scope` writes 9 keys incl.
  `solution_approach`/`open_questions` (the "authoritative design" the synthesizer needs);
  `_task_plan_handler._write_scope` writes 7 and **drops** them. Whether the design survives
  depends on which path fired.
- **HIGH — governance flags corrupt JSON output.** `_output_gov.py:270` / `_decision_gate.py:137`
  append `GROUNDING/GOVERNANCE FLAGS:` text to a result that is JSON under `output_schema`;
  downstream `json.loads` fails and silently downgrades exactly the flagged outputs to prose.
- **HIGH — `_build_agent_node.node_fn` is a ~300-line god-closure** (14 responsibilities, 6
  suppressed complexity lints); no seam to unit-test the branch matrix; hurts the eject story.
- **HIGH — `Any` at every important seam** (`planning_config`, `governance`, `state`,
  `provider_registry`, …) despite concrete types existing — strict typing is bypassed at the
  exact places the scope/plan-drift bugs live.
- **MEDIUM** — stringly-typed control flow (`"__delegated__:"`, `"__task_plan_complete__"`
  sentinels via `startswith` in 10+ sites; no `_errors.py` in the compiler); 3 divergent
  `dict→TaskPlan` loaders (one lossy); `SqliteSaver.from_conn_string` used as if it were a
  saver (resume may silently no-op); default `allow_all` governance + silent `custom`→allow.
- **Bright spot:** `resolver/` + `errors/` + `_output_schema.py` are reference-quality
  (structured errors, dispatch tables, `from_dict`, deterministic) — the standard the engine
  should be refactored toward.

### 2.B Runtime — providers & integrations (`model_providers/`, `mcp/`, `governance/`, …)

- **HIGH — the `num_ctx` fix (#399) is incomplete.** It stripped Ollama-only options only in
  the OpenAI adapter; **Anthropic** (`_anthropic.py:54`) and **Google** (`_google.py:180`)
  still do a raw `kwargs.update(request.options)`, so an Ollama-tuned topology crashes the
  same way when repointed at Claude/Gemini. Direct follow-up to this session's fix.
- **HIGH — OpenAI tool-calling is subtly broken.** Inbound `tool_input` is stored as a raw
  JSON **string** (violates the `dict` contract, `_openai.py:188`) — downstream code is split
  between defensive `json.loads` guards and raw use; outbound tool args use `str()` not
  `json.dumps` (`:146`) → invalid JSON that breaks multi-turn tool replay on all
  OpenAI-compatible providers.
- **MEDIUM** — Google sends the system prompt **twice** (fabricated user turn + native
  `system_instruction`); AGT audit sink **drops the M6 structured fields** (cost/verdict/
  tokens) the SQLite sink persists; **decision-skill governance fails open**; MCP
  `call_tool` has **no timeout/retry**; `get_session` has a check-then-start race; the MCP
  result cache is **dead code**; retry-classification is fragile substring matching (429s via
  httpx aren't retried).
- **Invariants verified clean by grep:** vendor SDKs only under `model_providers/`; AGT only
  under `governance/agt_provider.py`. `compression/_base.py` is a reference seam.

### 2.C Runtime — CLI / server / interface

- **HIGH — SSE progress cross-contamination.** `send_message` appends `on_progress` to a
  **process-global** `_progress_listeners` list (`server.py:738`); with ≥2 concurrent chat
  streams, users see each other's intermediate output. Confidentiality bug on multi-tenant serve.
- **HIGH — soft concurrency gate.** The semaphore is checked before the permit is acquired
  (acquire happens inside the already-spawned task), so a burst all sees "unlocked", all
  return `running`, then serialize — 429 is effectively dead and job creation is unbounded.
- **HIGH — blocking sync SQLite / JWKS in async handlers** (see T2).
- **HIGH — `cli/__init__.py` (2323) + `server.py` (1422) god-modules with inline logic** (T1).
- **MEDIUM (security)** — CORS `["*"]` + `allow_credentials=True` always on, not
  configurable from `serve` (D1); default-secure lives in the CLI, not `create_app` (D2);
  `"*"` scope bypasses the reserved-scope guard (D3); connector loop crashes (`KeyError`) on
  a malformed command despite a "never raises" docstring; webhook secret bypasses
  `resolve_secret_ref`.
- **No runtime invariant outright broken**, but `serve:admin` rewriting topologies + reload
  is in tension with invariant #6 (reserved `topologies:modify`) — worth an explicit design
  confirmation that admin-transport ≠ agent-obtainable.

### 2.D Control-plane panel (`packages/control-plane`)

- **HIGH — open-mode-by-default in production** (mint/deploy/approve unauthenticated when no
  tokens/OIDC; only a stderr warning). **Fix:** require explicit `--insecure-no-auth`, else
  refuse to start off-loopback.
- **HIGH — event-loop-blocking sqlite + four-locks-one-file** (T2/T5): the four stores share
  one file but hold four independent locks — negating WAL within a store and coordinating
  nothing across stores.
- **HIGH — cross-store operations aren't atomic:** `approve = register_version()` then
  `mark_approved()` (two stores, two txns) — a crash between leaves a published version whose
  proposal is still `pending`; `deploy` records `set_deployment` *before* the push, so a
  failed push leaves a phantom "deployed" drift record.
- **MEDIUM** — `GET /audit?limit=` unvalidated (**negative LIMIT = whole table** in sqlite;
  no pagination); `recent_audit` orders by a client-supplied string `ts`; `claim_queued`
  SELECT-then-UPDATE is only atomic via the in-process lock (double-dispatch across
  processes); `propose_from_gap` runs ~6 min synchronously in one request; `resolve_token`
  persists literal secrets at rest; `authorize` ignores HTTP method.
- **Bright spots verified:** `hmac.compare_digest` for operator tokens; connector tokens are
  256-bit, stored only as SHA-256 + fingerprint, shown once; aggregation forces `instance_id`
  from the principal (no spoofing); CORS deny-by-default, registered CORS-outermost.

### 2.E Control-plane cross-cutting / packaging

- **HIGH — the compose bundle's happy path doesn't connect.** UI on `:3000` + panel on
  `:8800`, no reverse proxy in the bundle, `NEXT_PUBLIC_CONTROL_PLANE_API` defaults empty
  (same-origin) and `next.config` defines no rewrites → every panel call from the browser
  404s after `docker compose up`. A compose smoke test would catch it.
- **HIGH — no migration path** for 3 of 4 stores (T5); **no e2e test** across the HTTP
  boundary for the growth loop or the inverted-transport (connector↔panel) contract — the
  most drift-prone surface (T3) has no cross-side test.
- **LOW** — non-reproducible panel image (`uv pip install` with no lockfile); partial
  CLI-flag/env-var coverage (`--oidc-audience`/`--oidc-jwks-url` have no env fallback).

### 2.F Fleet UI (`packages/control-plane-ui`)

- **CRITICAL(functional) — Runs "Refresh" is a no-op** and `/usage` is fetched twice
  (`runs/page.tsx:148`): the refresh poller's data is discarded; the two visible tables own
  separate pollers.
- **HIGH — `usePoll` is missing the safety a fetch layer needs:** no in-flight cancellation
  → **stale-overwrite race** on `id`/`kind` change (the artifact page hand-rolls a `cancelled`
  flag to compensate); **`loading` latches false** so refetch/param-change can't show a
  spinner and shows stale data; **no shared cache** → `listInstances` is polled by 3 hooks
  at once and the sidebar/dashboard can disagree.
- **MEDIUM** — the 4-way loading/error/empty ladder is copy-pasted ~9× (and already
  diverging); `FIELD` const + native `<select>` styling duplicated 5×; JSON `<pre>` viewer
  4×; `types.ts` hand-mirrors pydantic (T6); clickable table rows not keyboard-accessible;
  **operator-token auth is unreachable from the UI** (OIDC-only token store — a supported
  panel mode has no client path).
- **LOW** — theme FOUC; `confirm()`/`prompt()` for destructive flows; polling ignores tab
  visibility; two `statusVariant` idioms.
- **Bright spot:** TS quality is strong — `noUncheckedIndexedAccess` respected, no `any`,
  discriminated-union status maps. Adopt **SWR** behind the `api` client (which is well-built).

---

## 3. Reusable-library / structured-extraction proposals

Consolidated across both packages, highest leverage first.

1. **`swarmkit-serve-client` (+ canonical `VERB_ROUTES`)** — one typed async client for the
   runtime serve REST API (health/capabilities/jobs/run-poll/`/api`/usage) with header/base/
   error/poll written once, plus the single verb→(method,path,tier) table. Consumed by the
   panel (Mode A `_connector`/`_deploy`), the runtime connector (Mode B `connect.py`),
   `server._required_action`, and (generated) the UI. **Kills T3 + T4.** Lives in the runtime
   (or a small shared pkg) so the runtime needn't depend on the control plane.
2. **`_SqliteStore` persistence base** — connection/WAL/row-mapping/migration-registry +ONE
   decision on locking & async-offload, behind a dialect seam. Consumed by all **9** stores
   in both packages. **Kills T2's blocking + T5's duplication + the missing-migration hazard.**
3. **Generated API contract** — pydantic response models on the panel; `types.ts` +
   `KNOWN_VERBS` generated from the panel OpenAPI / `packages/schema`. **Kills T6.**
4. **Provider-adapter shared helpers** — `apply_options(kwargs, options, drop=_NON_NATIVE)`
   (fixes the incomplete `num_ctx` fix in one place), `tool_specs_to_openai_functions`,
   `image_to_data_url`, `map_stop_reason`, `parse_fenced_json`. Adapters shrink to genuinely
   provider-specific translation.
5. **Compiler primitives** — `RunContext` (run-scoped dir + `ContextVar` parent stack, fixes
   the CRITICAL), `ScopeStore` (one class, fixes scope drift), `Task.from_dict`/`TaskPlan.from_dict`,
   an `AgentStatus` enum + `langgraph_compiler/_errors.py` (replaces sentinels — best lever on
   eject), a `NodePipeline` of stages (decomposes the god-closure).
6. **Service layers** — `PanelService` (cross-store atomic approve/deploy), runtime
   `ArtifactService`/`JobService` + `WorkspaceRuntime.observability` (so CLI *and* server
   share readers). Enables T1's router/sub-app split.
7. **UI `useResource` (SWR) + component kit** — `<DataView>`/`<AsyncBoundary>`, `<JsonBlock>`,
   a form kit (`Select`/`Input`/`Label`/`Field`), `<StatusBadge>`. Kills the UI duplication
   and the `usePoll` race/latch/duplicate-fetch class at once.

---

## 4. Prioritized roadmap

### P0 — correctness & security (small, ship now)
1. **Complete the `num_ctx` fix** — strip Ollama-only opts in the Anthropic + Google adapters
   (extraction #4's `apply_options`). *Trivial; real bug shipped this session.*
2. **`RunContext`** — namespace run-state by `run_id`, `ContextVar` parent stack. *Fixes the
   CRITICAL cross-run corruption; unblocks serve.*
3. **Per-run SSE progress listener** — drop the module-global. *Confidentiality.*
4. **OpenAI tool-call JSON** — `json.dumps`/`json.loads` at the adapter seam.
5. **Default-secure at the app factory** — panel refuses open off-loopback; serve CORS not
   `*`+credentials; move the loopback guard into `create_app`.
6. **Fail-closed governance** — required decision-skill / missing-skill → `fail`, not `pass`;
   `custom` provider is a hard error; reject `"*"` in transport-token scopes.

### P1 — structural, high leverage
7. **Canonical `VERB_ROUTES` + `ServeClient`** (extractions #1) — one policy table + client.
8. **`_SqliteStore` base + move blocking off the loop** (extraction #2) — both packages; this
   is the *actual* concurrency hardening.
9. **Split the god-modules** — `_app.py`/`server.py` → APIRouters; `cli/__init__.py` → Typer
   sub-apps; introduce the service layers (extraction #6), incl. atomic approve/deploy.

### P2 — quality / DX / correctness tail
10. Generated API contract (extraction #3); provider shared helpers (#4).
11. UI: SWR `useResource` + component kit (#7); operator-token client path; a11y rows.
12. Compiler: `ScopeStore`, `from_dict`, `AgentStatus`+`_errors.py`, `NodePipeline`; strip the
    `document-writer`/CDT-Jira hardcoding (topology-as-data); JSON-safe governance flags.
13. Fix the compose same-origin bundle (add a proxy or ship a working default) + a smoke test.
14. Input/robustness tail: `?limit` bounds + pagination; `claim_queued` conditional UPDATE;
    background-job `propose_from_gap`; MCP tool timeout; connector malformed-command guard;
    migrations for the 3 stores; JWKS timeout+threadpool.

### Testing to add alongside
- e2e across the HTTP boundary for the growth loop and the **connector↔panel verb contract**
  (the quadruplicated map has no cross-side test).
- a `serve`-style **concurrent-run** test (would immediately surface the run-state CRITICAL
  and the SSE cross-talk).
- a **compose smoke test** (would surface the same-origin bundle defect).
- provider adapter branch tests via `httpx.MockTransport` (timeout/failed-job/malformed).

---

## 5. Overall assessment

The system is **well-documented, cohesively named, and strong in its "leaf" modules**
(resolver, errors, output-schema, compression seam, the typed UI, the auth primitives). The
debt is concentrated in **three god-modules**, **one duplicated policy/persistence/client
substrate** repeated across package and language boundaries, and a **permissive-by-default,
single-process concurrency model** that the roadmap language ("hardening for a real fleet")
claims but the code does not yet deliver. None of the Critical/High items are architecturally
hard to fix — they're mostly *extraction + one correct decision applied once*. The P0 set is a
few days of focused work and removes the genuinely dangerous items (cross-run corruption, SSE
cross-talk, the incomplete provider fix, open-by-default). P1's three extractions (`ServeClient`
+ verb table, `_SqliteStore`, the router/service split) would retire the majority of the
duplication and make the concurrency story real.
