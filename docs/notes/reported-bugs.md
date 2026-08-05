# Reported bugs — the ledger

Bugs found by running SwarmKit against real work, not by CI. Each entry records what broke, why the
existing tests missed it, and where its regression test now lives. Add to it when a bug is reported;
do not delete entries when they are fixed — the value is in the pattern.

**Read the pattern section at the bottom before adding.** These are not unrelated bugs.

## Open

Ordered by how much damage the bug does while looking fine.

| # | Bug | Component | Detail |
| --- | --- | --- | --- |
| — | A dedicated `/auth/callback` redirect URI (today `origin + pathname` forces wildcard IdP config) | webui | [oidc-client-config](../../design/details/oidc-client-config.md) |
| — | `jwt` identity (`sub`) matching no role member fails with a 403 that reads as unauthenticated | auth | [oidc-client-config](../../design/details/oidc-client-config.md) |
| 4 | `TaskSpec.context_files` set but never delivered | executor plumbing | [harness-parity-gaps](harness-parity-gaps.md) #4 |
| 5 | Relative image paths resolve nowhere inside the harness sandbox | sandbox | [harness-parity-gaps](harness-parity-gaps.md) #5 |
| 6 | A harness cannot see gateway tools once `allowed_tools` is set — the grant is written in skill ids, the gateway advertises `mcp__swarmkit__<server>__<tool>` | executor plumbing | [harness-parity-gaps](harness-parity-gaps.md) #6 |

## Fixed

### A display annotation broke every schema-bound harness run (1.156.0)

Bug 16. ` (+<N> bytes diff)` was appended to a successful harness result. For an agent whose
contract is JSON that makes the artifact unparseable, so `_enforce_harness_output_schema` reported
`(root): output is not valid JSON` instead of the field-specific errors it exists to produce. The
correction retries then re-sent the one message the agent could not act on until they exhausted —
two full agent sessions, unable to succeed by construction.

The same defect as the `[harness:<kind>]` prefix removed in 1.146.0, at the other end of the
string, and the comment directly beneath the offending line already documented it for the prefix.
`diff_bytes` had been in the `executor.result` audit payload seven lines above the whole time. On
non-schema runs the suffix was merely cosmetic, which is how it survived.

An existing test asserted the annotation WAS in the output — the bug was pinned in place by a test.

Also fixed the reported second half: `output_schema` on a harness was a post-hoc check with no
prompt-side statement, so the agent was never shown the shape and had to infer it from correction
feedback. It is now stated in the task as a delimited `<output-contract>`.

Tests: `test_harness_output_is_the_agents_own.py`.

### The dashboard read the wrong store, over a usage pipeline that was mostly unwritten (1.156.0)

Reported as stale analytics. Three layers.

The page read `/jobs` — in-memory, this serve process only, emptied on restart. Underneath,
`_record_run_usage` writes the per-model `run_usage` rows that ARE `/usage`, and it lived inside
`server/_jobs.py`, so only `POST /run/{topology}` reached it; the CLI, pipeline and chat recorders
each hand-rolled the job-level totals and wrote no breakdown. And those three read
`usage.cost_usd` directly, which token-only providers leave at zero — recording real runs as free.

Author's note: all three of those recorders are mine, added over 1.150.0–1.155.0. Each time I
added a writer and did not check what read it. That is the same mistake as the entry below.

Tests: `test_every_run_path_records_usage.py`, `packages/ui/lib/dashboard.test.ts`.
Design: [dashboard-real-data](../../design/details/dashboard-real-data.md).

### Chat recorded no job, and its audits sat under an id nothing referenced (1.155.0)

Chat was the last topology run recording nothing — and the most-used, since the conversational CLI
is the v1.0 on-ramp. Measured before the fix: 0 job rows, 2 audit rows, the audit rows under a
fresh random UUID.

Two problems that look like one from outside. No job row, so a conversation never appeared in
`/jobs` and its cost was attributable to nobody. And `ConversationManager.send` called
`runtime.run` **without a `thread_id`**, so every turn's events and its trace landed under a UUID
no conversation pointed at.

The second is the instructive one: auditing was never broken, only unfindable. A count of audit
rows would have looked healthy. This is the counterpart to the 1.153.0 entry below — there the
fields were written and not returned; here they were written and not addressable.

Tests: `test_chat_records_a_job.py`.
Design: [chat-run-recording](../../design/details/chat-run-recording.md).

### A job listed in history could not be opened (1.154.0)

`GET /jobs/{id}` read the in-memory `JobStore` only, while the history table it is reached from is
fed by the durable store. So a CLI run was listed and 404'd on click, as was every job from before
the last restart.

**1.150.0 created this by fixing the list**: it put rows on screen that nothing could open. A
second writer landed without the read path being asked whether it could serve what it wrote — the
same not-checking-the-other-half mistake as bug 14, where the grammar was fixed and the prompt
layer had to be pointed out separately.

Tests: `test_job_detail_reads_the_durable_store.py`.

### The audit API returned an event's header and dropped its content (1.153.0 / UI 0.32.0)

Reported alongside bug 15: audit entries in serve showed basic info with no detail of what the
audit was for or what was done. The page was not hiding anything — it was never sent anything.

`AuditEvent` was expanded in M6 to carry policy decision and reason, skill category, inputs,
outputs, verdict, reasoning, confidence, model, tokens, cost, duration and error. The store
persists all 25 columns and reads all 25 back. `_audit_event_to_dict` serialized **nine**. So every
governance decision rendered blank, making "allowed" and "never evaluated" identical on screen — in
the one record whose job is to tell them apart.

The regression test states the property against the table definition rather than a field list:
anything the store has a column for must reach the client. A column that is persisted and never
surfaced now fails a test instead of going unnoticed for four milestones.

Tests: `test_audit_api_returns_detail.py`, `packages/ui/lib/audit.test.ts`.
Design: [audit-detail-surfacing](../../design/details/audit-detail-surfacing.md).

### Pipeline stages left no job row (1.152.0 / UI 0.31.0)

Three ways to run a topology, two of them recorded it: `POST /run/{topology}` always did,
`swarmkit run` since 1.150.0, a pipeline stage never. A pipeline showed saga state in `/runs` and an
empty table in `/jobs`, so what a stage produced, what it cost and which trace belonged to it were
findable from neither view. The most expensive runs in the system were the only ones nobody could
look up.

Stages now record a job keyed by `stage_run_id(correlation, stage)` — already the LangGraph thread
and already the trace's `run_id` — carrying a `correlation_id` column so one run's stages are
selectable by column. The store is injected from the one storage service; a test asserts the stage
module never calls `storage_for_workspace`, because a second store would ignore the workspace's
config and could write jobs to a different backend from the one the UI lists.

Tests: `test_pipeline_stage_records_a_job.py`, `test_persistence.py`.
Design: [pipeline-stage-jobs](../../design/details/pipeline-stage-jobs.md).

### The tool loop emitted no audit events (1.151.0)

`skill.executed` came from a single site reached by the initial model call. The multi-turn loop
executes every subsequent call and emitted nothing, so coverage was structural: turn 1 audited, turns
2..n not. An agent making 16 calls was recorded as making 1 — and since the first turn is usually a
single orienting call, the log kept the least informative fraction of the run.

Nothing unauthorised was hidden (every skill was kb:read), but the guarantee that the log SHOWS what
was read was untrue, which is the property the log exists to provide. It also misled diagnosis:
during bug 14 an output citing tools the log did not show read as fabricated citations, when the
calls had simply happened in unrecorded turns. A false fabrication finding against a model is an
expensive kind of wrong.

Every dispatched skill call now emits the same event in the same shape, so existing readers are
unchanged, and `policy_decision` is stated rather than left null — a reader could not otherwise
tell "allowed" from "never evaluated".

Noted, not folded in: the loop's three built-in coordination tools (create-scope, read-task-result,
context_retrieve) remain unaudited. They are runtime built-ins with no skill id, IAM scopes or
provenance; recording them as `skill.executed` would put non-skills in the governance record.

### `swarmkit run` left no record (1.150.0)

There was exactly one writer of a job row — serve's JobService, behind `POST /run/{topology}`. A CLI
run built a runtime, executed, printed and exited, so it never appeared in the UI's jobs list or its
history, even though it had produced a trace and audit events. Anyone driving SwarmKit from the
terminal had no record of what they had run.

It now creates the row before the run and closes it on every exit path. The job id is the run's
THREAD id, which is also the trace's run_id, so the row points at its own trace — a link a
serve-started job cannot make, since it mints a separate id. An interrupted run is recorded as
`interrupted` and a deferred review as `deferred` rather than being flattened into `failed`: both
are resumable, and StatusBadge renders an unknown status as a muted pill, so accuracy costs nothing.

Recording is best-effort in one direction only — a store that will not open loses the record of a
run, never the run.

### output_schema suppressed every tool call on a model node (1.149.0)

`_build_completion_request` attached `response_format` whenever a schema resolved, regardless of
whether the same request carried tools, and the OpenAI adapter sent both. Under structured-output
enforcement the reply is constrained to the schema grammar and a `tool_calls` response is not in
that grammar — so a model handed both had no legal way to call a tool. It complied by filling the
schema with stubs saying it needed them.

Nothing errored, the document validated, the stage parked, and a reviewer was shown a well-formed
spec built from no evidence. A conformance check cannot catch that: the artifact is valid. Only the
audit shows the tools were never called. It also read as model flakiness, because OpenRouter routes
across providers that enforce json_schema inconsistently.

The schema now attaches only to a turn carrying no tools — which the runtime already had, since the
synthesis turn passes none. A schema-bound agent no longer returns directly from a tool-carrying
turn, so its document is still produced under the schema, and an agent that finishes with a schema,
tools available and zero calls now warns.

The prompt said the same thing one layer up, found while merging the grammar fix: "Return ONLY the
JSON object. No markdown, no explanation" was appended on EVERY turn, including the ones offering
tools, so a compliant model was still instructed not to call them. On a tool-carrying turn the
instruction now describes the FINAL answer and says to use the tools first; with no tools the strict
wording stands, since that turn has nothing to defer and the prompt is the only enforcement where a
provider's structured-output support is weak (1.149.1).

### The retry envelope read as prompt injection (1.146.0)

A regression from the decision-skill fix. With `post_output` skills running on harness executors,
the first revision was refused by the agent on safety grounds — it checked its worktree, found no
trace of the "prior turn" it was shown, and declined. The refusal parked as the stage artifact, so a
reviewer was asked to approve a safety refusal while the run reported success.

Three defects: `[harness:{kind}]` was baked into successful output (a display artifact the agent
never wrote, so replaying it fabricated authorship it could disprove); prior output was spliced raw,
unattributed and unbounded; and the envelope referred to "your previous attempt" while supplying
only the critique, to a process with no memory of it.

Prior output now gets the attributed, delimited, versioned framing `render_decisions` already gives
reviewer comments, and the prefix survives only on failure results, where the runtime really is the
speaker. The gate-driven rework path — `_prior_input`, which fed a stage its own draft unmarked by a
different route — was closed the same way in 1.147.0. See
`design/details/retry-envelope-attribution.md`.

### A transient error stranded an event as `claimed` forever (1.145.0)

`run_drive_loop()` has no error handling around `handle_event`, so any exception propagates out of
the loop, out of `asyncio.run()`, and the process exits — after the event was claimed and before it
was acked. It is then unrecoverable: `claim()` only ever selects `queued` rows, and there is no
`claimed_at`, no visibility timeout and no reclaim path. A restarted orchestrator polls forever past
an event it can never pick up, while the saga sits `active` with `updated_at` frozen at the crash.
Nothing reports an error — `pipeline status` shows a normal in-progress run.

The docstring says the opposite of what the code does: *"a crash re-drives from the store"*. A crash
is precisely the case that cannot re-drive.

Hit in practice by WSL's `autoProxy` pointing `HTTP_PROXY` at a dead port, so the orchestrator's
loopback `POST /pipelines/run-stage` raised `ConnectError`. The saga looked like a slow stage for
over an hour; recovery took direct SQL against `pipeline_events`, because re-emitting is refused
for an existing active saga and there is no gate to clear.

**Fixed** on all three fronts. The handler is wrapped, and a failure either returns to the queue or
is dead-lettered once `attempts` is exhausted — spaced by a poll interval, because releasing and
immediately re-claiming burns every attempt in milliseconds and is useless against the outage this
exists to survive. `claim()` also takes claims older than a visibility timeout, with the handler
heartbeating while it works, so a long stage is not stolen from a healthy worker; both paths share
one attempt counter, which bounds a crash loop as well as a failure loop. Dead-lettered events
surface in `pipeline status`, and `swarmkit pipeline retry-event` replaces the hand-written SQL.

Also: the loopback call to serve sets `trust_env=False` (httpx honours `HTTP_PROXY` even for
127.0.0.1, which is what killed it), and the default worker name is host+pid rather than the literal
`orchestrator-1` every process shared. See `design/details/orchestrator-event-recovery.md`.

### A failed stage's error became the next stage's input (1.139.0)

When a stage's agent fails, the failure message is stored as that stage's **output** and handed to
the next stage as its **input**. On WMS-1: triage output was 46 bytes reading
`[harness:claude-code] failure: no result event`, and design's input was *the same 46 bytes*. Design
replied "I'm ready to help — what would you like to work on?", the gate parked on that, and a human
was asked to approve work that was never attempted. The saga reported `parked` throughout.

Three things compound: the pipeline advances **past** a failure; the original error is destroyed
when design's output replaces it; and money is spent running an agent on an input that cannot
succeed.

**Correction to the report.** It suggested reusing `_is_error_passthrough()`, on the grounds that
the runtime already recognises these strings. It does not recognise *this* one: the predicate matches
`Error:` / `Tool error:` / `ToolError:` and returns False for
`[harness:claude-code] failure: no result event`. Reusing it would have shipped looking like a fix
and let the bug straight through. A harness failure is not an exception either, so there was nothing
structural to check — hence `node_errors`, where the node states failure in a field.

**Fix direction:** a stage result carrying an explicit `ok: bool` / `error: str | None` rather than
a string prefix match, so a failed stage fails the saga (or parks **on the failure**), the error
survives as the failure reason, and the gate UI can say "triage failed" instead of rendering an
error as a document. Related: gap #2 means the `post_output` conformance check that would have
rejected a 46-byte output never ran either — the two together are why this reached a human unnoticed.

**Fixed** by marking failure structurally (`node_errors`) rather than by string prefix — the
suggested `_is_error_passthrough` reuse would not have matched the harness failure string at all.
A failed stage now fails the saga and does not open a gate.

### The MCP gateway dropped `ImageContent` (1.138.0)

`_to_content()` is text-only by construction: it reads `.text` off each block, and `ImageContent`
carries `.data` + `.mimeType` instead — so every image block is skipped. When a response is *only*
images, the fallback stringifies the response and the agent receives
`Image: screen1.png (.png, 14960 bytes base64)`. Verified: only `TextContent` is imported
(`_gateway.py:119`), so there is currently no type available to re-emit an image with.

A **model** node handles images correctly; the loss is specific to the gateway, which is the
harness's only route to MCP. So the same skill on the same workspace works on a model node and
silently degrades on a harness — and it degrades to something that reads as a successful call.

This is the other half of the image failure that 1.135.0 made *visible*. That release ensured a
failed tool call is traced as a failure; this one is worse, because the call genuinely **succeeds**
— the bytes are read and then discarded in the last step before the harness sees them.

**Fix direction:** re-emit image blocks (MCP's `CallToolResult` permits mixed content and Claude
Code renders them). If images are deliberately not forwarded, say so explicitly — a `DENIED`-style
message — rather than a repr that reads like success.

**Fixed** by re-emitting image blocks alongside text using the same `type == "image"` discriminator
the model path uses, so the harness and model paths agree about the same block.

### `/jobs` showed only in-flight work (UI 0.30.0)

The page read only `/jobs` — the in-memory `JobStore`, which holds this serve process's jobs and is
empty after a restart. `/jobs/history` existed server-side the whole time and nothing called it, so
a restart erased the visible record of every run, and the durable token usage and cost the store had
been recording were never displayed anywhere.

Now two sections: **Running now** (live, polled every 3s) above **History** (durable, every 15s,
with tokens and cost). The catch is that a job is written to BOTH stores at creation, so the lists
overlap while it runs — history excludes anything shown live rather than printing it twice. Cost
shows `-` when unrecorded rather than `$0.00`, since an unmeasured run and a free one are different
things. See `design/details/jobs-history-ui.md`.

### Concurrent `create_all` crashed one process at startup (1.144.0)

Found while investigating two CI failures — `table conversations already exists` and
`table fleet_enrollment_tokens already exists` — that both passed on re-run. Not a test artifact:
`metadata.create_all` is check-then-create, so two processes starting together both see a table
absent and both issue `CREATE`, and one dies. Every comment in the codebase called it "idempotent",
which is true within one process and false across two — and two is the normal case (`swarmkit serve`
plus `swarmkit orchestrator` share a store, as do replicas of either, and the panel shares one
database across four stores).

Reproduced at **12/12 trials** with six processes against one SQLite file; 0/12 after.

All seven runtime call sites and the control-plane's now go through a helper that **verifies rather
than swallowing**: a losing racer is fine only if the tables really are there afterwards, so a
persistently missing table still raises and any non-duplicate error propagates untouched. The panel
keeps its own copy — it never imports the runtime — with a contract test over the boundary, and an
AST guard stops a new store reintroducing the race.

The second cost is worth naming: a test that fails randomly trains people to press re-run without
reading, which is how a real failure gets waved through.

### `output_schema` was ignored on the harness path (1.143.0)

`_harness_node.py` contained zero references to `output_schema`, so together with gap #2 a harness
agent had neither a schema constraint nor a post-hoc check — the two independent mechanisms that
would each have caught the `wms-design` markdown.

Now validated before the decision-skill gate, with the correction driven back through the harness
using field-specific errors; exhaustion annotates and emits `output.schema_violation` rather than
passing silently. Only an explicitly declared schema is enforced — the model path's worker platform
default would have imposed a findings-schema on every harness worker, including a `developer`
archetype that produces a diff, failing every run at full harness cost. See
`design/details/harness-output-schema.md`.

### Decision skills never ran on a harness executor (1.142.0)

A `required: true` `post_output` skill bound in a topology was never invoked on an agent whose
`executor.kind` is `harness`: `node_fn` returned to the harness runner before reaching any gate, so
`_ds_bindings` was computed and discarded. On `wms-design` the agent returned markdown where a JSON
object was required, `spec-conformance` never ran, and the markdown became the run's final output.

Silent and inverted from the safe direction: `required: true` meant nothing, `swarmkit validate`
reported no error (the binding is structurally valid), the trace showed a normal successful node,
and the behaviour changed with `executor.kind` alone. Compounded by gap #3 — `output_schema` is also
ignored on the harness path — so neither of the two independent mechanisms that would have caught a
non-conforming output was in play.

`pre_input` and `post_output` now run for every executor kind, with the retry driven by the agent's
own executor. `checkpoint` / `pre_synthesis` stay model-path-only: they fire inside task-plan
execution, which a harness node never does, so claiming them would be claiming a fix that does
nothing. See `design/details/harness-decision-skills.md`.

### The portal's OIDC client could not be configured (1.141.0 / webui 0.7.0)

`client_id` came from `NEXT_PUBLIC_OIDC_CLIENT_ID`, which Next.js inlines at **build** time. In the
published artifact it was never inlined — the bundle carried the literal source text
`NEXT_PUBLIC_OIDC_CLIENT_ID)?t:""`, a live property read against the browser `process` polyfill whose
`env` is `{}` — so it evaluated to `""` on every load and `signinRedirect()` went out with
`client_id=`. There was no runtime escape hatch in a static export: no `env.js`, no `window.__ENV`.

So the published wheel could not be pointed at any identity provider, and an operator who configured
`provider: jwt` correctly still could not sign in, with nothing in the UI or the server log saying
why. The original reasoning — the serve validates tokens, it does not own the browser's client id —
is right about ownership and wrong about distribution: a per-deployment value cannot live in a
constant fixed at publish time.

`client_id` and `scope` are now advertised on `/auth-info` alongside `issuer` and `audience`
(absent, not empty, when unconfigured, so a self-built UI's fallback still applies). The portal
prefers the served value, refuses to start a redirect it knows will fail and names the missing
setting, and `jwt` mode gained the token-entry path `api_key` mode always had — so a broken redirect
flow no longer locks the portal completely. See `design/details/oidc-client-config.md`.

Verified in the **built bundle**, since the build is where this bug lived.

### `auth: none` can name its operator (1.140.0)

`NoneAuthProvider` handed out `client_id="anonymous"` with `scopes={"*"}` and an `authorize()` that
returned True unconditionally. The approval engine does not consult scopes — it matches `client_id`
against `members:` in `swarm/roles.yaml` — so `anonymous` was refused not for lacking authority but
for being nobody: a mode that permits every action could not perform the one action that depends on
identity, and a single operator had to stand up an IdP to click Approve on their own laptop.

This was the root cause behind the dropped-comment bug (1.137.0): because the authenticated resolve
path was unusable locally, callers fell back to the break-glass event route, which was the one
discarding comments.

Optional `server.auth.config.identity` / `identity_name`, defaulting to `anonymous`. Naming grants
no capability the mode does not already give (asserted by test). The loopback refusal still applies,
the identity still carries `provider="none"` so the audit can tell asserted from authenticated, and
startup warns when the name matches no role member — a role-check 403 is otherwise
indistinguishable from being unauthenticated. See `design/details/none-auth-named-operator.md`.

### `rework` discarded the reviewer's comment (1.137.0)

A reviewer requested changes with a comment; the stage re-ran knowing nothing about it, reproduced
substantially the same output, and the reviewer had no way to tell why their feedback had no effect.
`_rework()` received the event `data` — carrying the comment as `detail` — and never read it. The
`add("resumed", ...)` call then overwrote the timeline detail with a fixed string, so the comment
was not preserved for a later human reader either. The docstring claimed the opposite of the code.

Why it hid: comments **do** reach the agent by a second route — `/review/{item}/resolve` mints a
resolved item that `decisions_for_gate` renders — so the feature is real and works whenever the
reviewer is an authenticated identity. Under `auth: none` that endpoint 403s (`approvals:resolve` is
reserved for human identity), callers fall back to enqueuing the controller event, and the comment
travels only in `data["detail"]`. Configuration-dependent silent data loss: the same UI action
delivered or discarded the comment depending on the auth provider, with nothing different for the
reviewer to see.

Both routes now converge on `render_decisions`, the break-glass one labelled
`operator-override` so nothing is misrepresented as an authenticated review. The comment is stamped
with the round and artifact it was written against, and carried in the timeline's existing JSON
column — there is no migration facility, so a new column would break existing deployments on their
next insert. Tests: `test_rework_comment.py`.

Observed on WMS-1: a domain correction about cartons and `getTaskList` never reached the design
agent.

### `swarmkit storage migrate` left Postgres unwritable (1.136.0)

Rows were copied **with their original primary keys** and the owning sequences never advanced, so a
sequence sat at 1 while `max(id)` was 14. The next insert reused a live id:

```
UniqueViolation: duplicate key value violates unique constraint "pipeline_events_pkey"
DETAIL: Key (id)=(1) already exists.
```

`pipeline_events` is the blocking table — every `pipeline emit` writes there — so after a migration
**no pipeline could start at all**.

Why it survived: the migration reported success with row counts; every *read* worked, so runs
displayed correctly in the UI and in `pipeline sagas`; the failure appeared later on an unrelated
write, naming a constraint rather than the migration. And `swarmkit serve` kept advising operators
to run the very command that broke the store.

Fixed by re-syncing every sequence owned by a column, enumerated from `pg_depend` so tables added
later are covered without anyone remembering. The migration now **fails** if a sequence is still
behind afterwards — a migration that leaves the store unable to accept an insert must not report
success. Empty tables keep `nextval = 1` rather than burning id 1 to `COALESCE(max, 1)`.

Found while reproducing: `psycopg` returns `rowcount == -1` for an executemany, so a migration that
copied all 14 rows announced `0 copied, 14 already present` — byte-identical to a re-run that did
nothing. Counts are now taken either side of the insert.

Tests: `packages/runtime/tests/test_storage_migrate_sequences.py` (needs
`SWARMKIT_TEST_POSTGRES_URL`; sequences do not exist in SQLite, so this could not have been caught
by a mocked test).

### Harness tool outcomes discarded (1.135.0)

A design agent described UI screens it had never seen, three runs running, because the image tool
returned nothing and the trace rendered `view-screenshot ✓` either way. Three of four adapters
mapped no tool outcome at all; `ExecToolCall.status` had no shared vocabulary; and
`ToolCall.result_length` was given the *argument* length, so a tool that returned nothing showed a
healthy number because the path was long. See `design/details/harness-tool-outcomes.md`.

### Per-stage traces overwrote each other (1.133.0)

Every stage of a pipeline used `thread_id=correlation_id`, which is also the trace's `run_id` and
the file name — so a three-stage run left one trace, the last stage's. Tests:
`test_stage_run_id.py`.

### Earlier (1.123.0–1.132.0)

Storage config never read; a degraded checkpointer reporting success; `--mcp-config` swallowing the
server list as filenames; `str(engine.url)` masking the password it was asked to print;
`swarmkit orchestrator` unregistered by a decorator separated from its function. Tests:
`test_reported_bugs.py`, `test_store_factory.py`, `test_cli_command_registration.py`,
`test_engine_url_password.py`.

## Near misses

### The portal fix that would not have shipped (caught at v1.145.0)

The jobs-history page was in the built bundle, but `swarmkit-webui` was still 0.7.0 — already on
PyPI — so `publish_if_new` would have skipped it. Every workflow would have gone green and no user
would have received the fix. `packages/ui` and `packages/webui` have separate versions and the
former does not publish anything. Rule written down in [release-version-discipline](release-version-discipline.md).

### Four features that shipped unreachable (found at v1.145.0)

`swarmkit-schema` was never republished after 2026-07-27 — the version sat at 1.23.0 through six
releases while `packages/schema/schemas` kept changing. So `server.auth.config.identity`,
`client_id` / `scope`, `storage.artifacts` and the adapters' `*_map` tables were all rejected by the
schema every installed user actually had; the runtime shipped bundled adapters its own published
schema refused. Merged, tested, reviewed, released — and unreachable.

Found because an operator reported the named-operator config being rejected and I checked whether
the schema in the repo matched the one on PyPI. Fixed in 1.145.1 / schema 1.24.0, with
`scripts/check_publishable.py` so the next one fails the release instead of shipping.

## The pattern

Nearly every entry above is the same bug wearing different clothes:

> **Information exists, nothing surfaces it, and absence renders as success.**

The sequence was knowable. The tool's failure was in the stream. The checkpointer knew it had
degraded. The trace knew it was overwriting a file. The gateway had the image bytes in hand. The
compiler already recognised a failed stage's output as not-agent-reasoning, and chained it onward
anyway. In each case something computed the truth and then dropped it, and the layer above printed a
confident status line over the gap.

Two working rules follow:

1. **A component that cannot do its job must say so louder than it says "done".** Prefer failing the
   operation to completing it in a degraded state — `storage migrate` now refuses to report success
   over a store it has left unwritable.
2. **"Unknown" is not "fine".** Where a status can be unreported, model it as a third value rather
   than folding it into the healthy one. That is why `ExecToolStatus` is `""` / `ok` / `error`.
3. **Do not infer status from prose.** Bug 9 reported failure only as output *text*, so every
   caller had to guess; the obvious string check did not even match the string in question. Bug 10
   read the image bytes and dropped them one step before delivery. Where a caller needs to know
   whether the work happened, the producer should say so in a field — `node_errors`,
   `ExecToolStatus` — not in something a human happens to be able to read.

A fourth rule, from the two most recent entries: **a producer that writes a field nobody reads is
the same bug one layer up.** The audit store persisted 25 columns and the API returned 9; the
pipeline stage computed a run id, an output and a cost and wrote none of them down. Neither failed —
both rendered as a thin-looking row that a reader would take for the whole truth. Where a store and
a reader are separated by a serializer, state the completeness as a test against the schema, not as
a list somebody remembers to extend.

And a testing rule, learned repeatedly: **these bugs are invisible to mocked tests.** The sequence
bug needed a real Postgres; the governance bug needed a real run. Unit tests are for coverage; a
live pipeline is for confidence.
