# Fix plan — architecture review remediation (July 2026)

Execution checklist for remediating every finding in
[the architecture review](2026-07-control-plane-and-runtime-review.md). Ordered as a
sequence of focused, independently-tested PRs. Each PR is branched off fresh `main` and
merged before the next (to avoid stacked-squash conflicts). Check items off as they land.

Legend: `[ ]` todo · `[x]` done (PR #) · `[~]` partial.
> **Status: all P0 merged (#410–#416); P1-G control-plane store base (#417); P1-I god-module
> split complete — panel `_app.py` (#424), `server.py` (#425), `cli/__init__.py` (#426);
> P2-J/N/O provider + robustness + single-origin bundle (#418–#422). Deferred as dedicated
> follow-ups: PR-H (cross-package `ServeClient` + verb table — needs a shared package), PR-K
> (compiler `ScopeStore`/`from_dict`/`AgentStatus` + topology-as-data cleanup), PR-L (UI SWR
> migration + component kit), PR-M (generated API contract), the runtime `_SqliteStore` half
> of PR-G, and the service-layer extraction noted under PR-I below.**


---

## P0 — correctness & security (small, high-value)

- [x] **PR-A (#410) — `apply_options` + complete the `num_ctx` fix.** Lift `_OLLAMA_ONLY_OPTIONS`
  into `model_providers/_types.py`; add `apply_options(kwargs, options, extra, *, drop)`;
  apply the drop-set in the Anthropic and Google adapters (OpenAI already does). Tests: the
  drop-set is applied by every non-Ollama adapter; genuine params survive.
- [x] **PR-B (#411) — OpenAI tool-call JSON.** `_from_openai_response` parses `tool_input` to a dict
  (`json.loads`, guarded); outbound tool-call args + tool-result use `json.dumps`, not
  `str()`. Tests: round-trip a tool call through the adapter.
- [x] **PR-C (#412) — per-run SSE progress listener.** Replace the process-global
  `_progress_listeners` with a per-run listener threaded through the runtime/`ContextVar`.
  Test: two concurrent conversations don't cross-emit.
- [x] **PR-D (#413) — `RunContext` (run-state corruption, CRITICAL).** Namespace run-state by
  `run_id`/`thread_id` (drop the literal `current/`); replace the module-global parent
  pointer with a `ContextVar` stack; thread `workspace_root`+`run_id` into
  `_scope_path`/`_find_tasks_json`/`_check_scope_exists`/synthesis. Test: two concurrent
  runs keep separate scope/plan.
- [x] **PR-E (#414/#415) — default-secure app factories.** Panel `create_app` refuses open off-loopback
  unless `--insecure-no-auth`; move the runtime serve loopback/auth guard into `create_app`;
  `serve` CORS defaults to none/loopback (never `*`+credentials) with a `--cors-origin` flag.
  Tests: open-off-loopback refused; CORS default is not wildcard-with-credentials.
- [x] **PR-F (#416) — fail-closed governance.** Required decision-skill / missing-skill → `fail`
  (not `pass`); `provider: custom` is a hard error not a silent allow-all; reject `"*"` in
  transport-token `reserved_violations`. Tests: each fail-closed path.

## P1 — structural extractions (retire the substrate duplication)

- [x] **PR-G — shared sqlite connection base, both packages (#417 control-plane, #438 runtime).**
  Control-plane `_sqlite_base.SqliteStore` (#417); runtime `_sqlite.wal_connection`/`bootstrap`
  shared by the persistence/audit/telemetry/notifications stores (#438). **Deferred:** the async
  offload (blocking sqlite → `asyncio.to_thread`) — folded into the SQLAlchemy migration below,
  which moves the async audit provider onto an async engine.
- **Postgres backend (new, user-requested — design/details/postgres-backend.md).** SQLAlchemy Core,
  one impl per store, SQLite default + Postgres for distributed deploys. **PR-1 (#440):** runtime
  persistence store. **PR-2 (#442):** runtime audit provider (sync SQLAlchemy engine, async
  signatures kept — event-loop-safe; `SQLiteAuditProvider`/`PostgresAuditProvider`; audit follows
  the store backend via `audit_provider_for_path`). The **runtime** now runs fully on SQLite or
  Postgres. **PR-3 (#447):** control-plane stores (registry/artifacts/proposals/aggregation) on
  SQLAlchemy Core — one `_tables.py` metadata + engine-holding `Store` base (`_store_base.py`,
  replaces `_sqlite_base`) shared by all four stores via one engine; `_store_factory.create_registry`
  selects the backend (`SWARMKIT_CONTROL_PLANE_STORE_BACKEND`/`DATABASE_URL`). `claim_queued` is
  dialect-aware — `SELECT … FOR UPDATE SKIP LOCKED` on Postgres, `BEGIN IMMEDIATE` (AUTOCOMMIT-driven)
  on SQLite — so the cross-process no-double-dispatch guard holds on both; upserts go through a
  dialect `INSERT … ON CONFLICT` helper; aggregation `payload` is a JSON column so rollups extract in
  SQL on both dialects (RETURNING-based dedup counting, since psycopg reports `rowcount=-1` for
  `ON CONFLICT`). Standalone (adds its own `sqlalchemy`+`psycopg` deps, no runtime import). The
  **control-plane** now runs fully on SQLite or Postgres. Guarded by the existing 141-test suite +
  a cross-process claim concurrency test + a `SWARMKIT_TEST_POSTGRES_URL`-gated integration suite
  (verified against a real Postgres 16). The whole **Postgres backend feature is now complete**.
- [x] **PR-H (#436) — canonical verb table + `ServeClient` + cross-package contract.** Runtime
  `connect._VERB_ROUTES` is now the public canonical `VERB_ROUTES` (+ `DEPLOY_PLURAL`/`DEPLOY_TIER`/
  `verb_tiers()`). Control-plane got a `ServeClient` (async; bearer/base/error-map + `ok()`) that
  `_connector.py`/`_deploy.py` now express calls over — the 5 copy-pasted HTTP boilerplate sites
  collapsed to one; `resolve_token`→`resolve_secret_ref` (deleted the old name). **Contract test**
  (`test_verb_contract.py`, importorskip-guarded) asserts panel `VERB_TIERS` == runtime `verb_tiers()`
  + `DEPLOYABLE` == `DEPLOY_PLURAL` + tier ranks — one table, enforced by CI, so they can't drift.
  13 `ServeClient` unit tests. **Deferred:** a single `ServeClient` shared *across* the runtime↔panel
  boundary (needs a shared published package; the panel stays standalone per D1) and UI `KNOWN_VERBS`
  generation — the contract test already prevents drift without them.
- [x] **PR-I — split the god-modules (I1 #424 / I2 #425 / I3 #426).** Panel `_app.py` (813→163)
  → `_schemas` + `_fntypes` + `_routes_{registry,artifacts,growth}`; `server.py` (1433) →
  `server/` package (`_config`/`_schemas`/`_jobs`/`_helpers`/`_mcp`/`_routes_*`/`_app`);
  `cli/__init__.py` (2332) → `_app` + `_common` + `_cmd_*` (flat command namespace preserved).
  Behaviour-preserving file splits; import surfaces unchanged; each validated by the existing
  suites (107 panel / 110 server / 77 CLI) + mypy.
- [x] **PR-I service layer — extract business logic behind services (#428, #430, #432, #434).**
  The review also asked for a service seam so logic is unit-testable without HTTP/Typer. **Panel:**
  `GrowthService` (#428) — growth-loop propose/approve/reject; **approve is now atomic** (claim-first,
  so concurrent approvals can't double-publish duplicate versions), 14 unit tests. `DeployService`
  (#430) — governed deploy, shared `ServiceError` taxonomy, ordering invariant (intent recorded only
  after a successful push) now unit-tested, 7 unit tests. **Runtime:** `JobService` + `ArtifactService`
  (#432) — run/webhook (de-duplicated `resolve_topology`) and artifact CRUD lifted out of the
  `server/` route closures, 14 unit tests. **CLI:** `WorkspaceRuntime.observability` facade (#434) —
  the `.swarmkit/` layout + audit/JSONL read logic behind logs/status/why/ask/debug/trace/checkpoints;
  those commands got their **first tests** (14 facade unit + 14 CliRunner e2e).

## P2 — quality / DX / correctness tail

- [x] **PR-J (#419) — provider adapter shared helpers.** `tool_specs_to_openai_functions`,
  `image_to_data_url`, `map_stop_reason`, `parse_fenced_json`; fix Google double-system-prompt;
  per-adapter retry classification; MCP `call_tool` timeout + start lock; remove dead MCP cache.
- [~] **PR-K — compiler primitives + topology-as-data cleanup.** **K2 (#439):** `Task.from_dict`/
  `TaskPlan.from_dict` — one loader replacing three inlined loops, fixing a reload that dropped
  timing/tool-call fields. **K1 (#444):** `ScopeStore` — one `scope.json` writer/reader; fixes the
  dropped `solution_approach`/`open_questions` writer + the stale-`current/`-path decision-gate
  reader. **K4a (#445):** stripped hardcoded CDT/Jira domain nouns from framework prompts/docstrings.
  **K3 (#448):** `_sentinels.py` — `TaskStatus`/`AgentStatus` `StrEnum`s + delegation helpers
  (`make_delegated`/`is_delegated`/`delegated_child`/`is_task_plan_status`/`TASK_PLAN_ACTIVE`)
  replacing the `__task_plan_*__`/`__delegated__:`/`__delegated_parallel__`/`__done__` magic strings
  and the bare `pending`/`in_progress`/`completed`/`failed` status literals across the compiler
  (`_task_plan`/`_task_plan_handler`/`_task_executor`/`_delegation`/`_compiler`/`_prompts`/
  `_tool_loop` + `_workspace_runtime`). `StrEnum` so persisted `tasks.json`/checkpoints round-trip
  unchanged (member == literal, JSON-serialises as literal); guarded by that contract in
  `test_sentinels.py` + the full 1080-test suite. **Pending:** K4b make the `self`/`document-writer`/
  `synthesizer` role literals topology/archetype-configurable; JSON-safe governance-flag attachment.
- [ ] **PR-L — UI SWR kit.** `useResource` (SWR) replacing `usePoll` (fixes race/latch/dup-fetch/
  no-op-refresh); `<DataView>`/`<JsonBlock>`/form-kit/`<StatusBadge>`; operator-token client
  path; keyboard-accessible rows; back `InstanceProvider` with the shared cache.
- [ ] **PR-M — generated API contract.** Panel returns pydantic response models; `types.ts` +
  `KNOWN_VERBS` generated from the panel OpenAPI / `packages/schema`; CI drift check.
- [x] **PR-N (#418/#422, partial: audit-limit + atomic deploy + atomic claim; deferred: literal-token-ref reject, background propose_from_gap, audit ts) — control-plane robustness tail.** Atomic approve/deploy (record deployment only
  on push success; single-txn approve); `?limit` bounds + pagination; `claim_queued`
  conditional UPDATE; background-job `propose_from_gap` (202 + poll); reject literal
  `token_ref`; method in `authorize`; server-side audit `ts`.
- [x] **PR-O (#420/#421: compose same-origin + smoke-tested, connector malformed-command guard, JWKS threadpool; deferred: webhook resolve_secret_ref, AGT M6 fields) — infra/robustness.** Compose same-origin fix (proxy or working default) + a
  compose smoke test; migrations for the 3 stores (covered by PR-G's migration registry);
  JWKS timeout+threadpool; connector malformed-command guard + backoff; webhook secret via
  `resolve_secret_ref`; AGT audit sink forwards M6 fields.

## Cross-cutting tests to add
- e2e across the HTTP boundary for the growth loop + the connector↔panel verb contract.
- a `serve`-style concurrent-run test (guards PR-C/PR-D regressions).
- a compose smoke test (guards PR-O).
- provider adapter branch tests via `httpx.MockTransport`.

## Notes
- Every runtime change bumps `packages/runtime/pyproject.toml`; every panel change bumps
  `packages/control-plane/pyproject.toml`; tag after merge per the release checklist.
- Branch off fresh `main` per PR; merge before the next.
