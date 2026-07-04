# Fix plan — architecture review remediation (July 2026)

Execution checklist for remediating every finding in
[the architecture review](2026-07-control-plane-and-runtime-review.md). Ordered as a
sequence of focused, independently-tested PRs. Each PR is branched off fresh `main` and
merged before the next (to avoid stacked-squash conflicts). Check items off as they land.

Legend: `[ ]` todo · `[x]` done (PR #).

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
- [ ] **PR-I — split god-modules + service layer.** `_app.py` → `APIRouter`s + `PanelService`
  (atomic approve/deploy); `server.py` → routers + `ArtifactService`/`JobService`;
  `cli/__init__.py` → Typer sub-apps + `WorkspaceRuntime.observability`. Likely I1/I2/I3.
  Tests: service methods unit-tested without HTTP.

## P2 — quality / DX / correctness tail

- [ ] **PR-J — provider adapter shared helpers.** `tool_specs_to_openai_functions`,
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
- [ ] **PR-N — control-plane robustness tail.** Atomic approve/deploy (record deployment only
  on push success; single-txn approve); `?limit` bounds + pagination; `claim_queued`
  conditional UPDATE; background-job `propose_from_gap` (202 + poll); reject literal
  `token_ref`; method in `authorize`; server-side audit `ts`.
- [ ] **PR-O — infra/robustness.** Compose same-origin fix (proxy or working default) + a
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
