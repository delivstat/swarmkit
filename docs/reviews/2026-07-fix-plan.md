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

- [~] **PR-G (control-plane store base done; runtime stores pending) — `_SqliteStore` base + async offload.** Shared base (connection/WAL/
  row-mapping/migration-registry) behind a dialect seam; move blocking sqlite off the event
  loop (`asyncio.to_thread` or `def` handlers). Apply to the 9 stores across both packages
  (may split into G1 runtime / G2 control-plane). Tests: WAL+migration on every store; a
  concurrency test that the loop isn't blocked.
- [ ] **PR-H — canonical `VERB_ROUTES` + `ServeClient`.** One verb→(method,path,tier) table +
  a typed async serve client (header/base/error/poll once). Consumed by runtime `connect.py`,
  control-plane `_connector.py`/`_deploy.py`, `server._required_action`; UI `KNOWN_VERBS`
  aligned/generated. Delete `_connector.resolve_token` in favour of `resolve_secret_ref`.
  Test: one table drives all sides; a cross-side contract test.
- [x] **PR-I — split the god-modules (I1 #424 / I2 #425 / I3 #426).** Panel `_app.py` (813→163)
  → `_schemas` + `_fntypes` + `_routes_{registry,artifacts,growth}`; `server.py` (1433) →
  `server/` package (`_config`/`_schemas`/`_jobs`/`_helpers`/`_mcp`/`_routes_*`/`_app`);
  `cli/__init__.py` (2332) → `_app` + `_common` + `_cmd_*` (flat command namespace preserved).
  Behaviour-preserving file splits; import surfaces unchanged; each validated by the existing
  suites (107 panel / 110 server / 77 CLI) + mypy. **Follow-up (not done):** the *service-layer*
  extraction the review also called for (`PanelService.approve/deploy` atomic, `ArtifactService`/
  `JobService`, `WorkspaceRuntime.observability`) so business logic is unit-testable without
  HTTP/Typer — deferred as its own PR now that the modules are separated.

## P2 — quality / DX / correctness tail

- [x] **PR-J (#419) — provider adapter shared helpers.** `tool_specs_to_openai_functions`,
  `image_to_data_url`, `map_stop_reason`, `parse_fenced_json`; fix Google double-system-prompt;
  per-adapter retry classification; MCP `call_tool` timeout + start lock; remove dead MCP cache.
- [ ] **PR-K — compiler primitives + topology-as-data cleanup.** `ScopeStore` (one writer,
  keeps `solution_approach`/`open_questions`); `Task.from_dict`/`TaskPlan.from_dict`;
  `AgentStatus` enum + `langgraph_compiler/_errors.py` (replace string sentinels); JSON-safe
  governance-flag attachment; move `document-writer`/synthesis-role + strip "CDT/Jira" domain
  text out of the framework into topology/archetype metadata.
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
