# Reported bugs — the ledger

Bugs found by running SwarmKit against real work, not by CI. Each entry records what broke, why the
existing tests missed it, and where its regression test now lives. Add to it when a bug is reported;
do not delete entries when they are fixed — the value is in the pattern.

**Read the pattern section at the bottom before adding.** These are not unrelated bugs.

## Open

Ordered by how much damage the bug does while looking fine.

| # | Bug | Component | Detail |
| --- | --- | --- | --- |
| 9 | A failed stage's error string is handed to the next stage as its input | stage chaining | [below](#9-a-failed-stages-error-becomes-the-next-stages-input) |
| 10 | MCP gateway drops `ImageContent`, so a harness agent can never see an image | `mcp/_gateway.py` | [below](#10-the-mcp-gateway-drops-imagecontent) |
| 3 | `output_schema` ignored on the harness path | `_harness_node.py` | [harness-parity-gaps](harness-parity-gaps.md) #3 |
| — | A dedicated `/auth/callback` redirect URI (today `origin + pathname` forces wildcard IdP config) | webui | [oidc-client-config](../../design/details/oidc-client-config.md) |
| — | `jwt` identity (`sub`) matching no role member fails with a 403 that reads as unauthenticated | auth | [oidc-client-config](../../design/details/oidc-client-config.md) |
| 4 | `TaskSpec.context_files` set but never delivered | executor plumbing | [harness-parity-gaps](harness-parity-gaps.md) #4 |
| 5 | Relative image paths resolve nowhere inside the harness sandbox | sandbox | [harness-parity-gaps](harness-parity-gaps.md) #5 |
| — | `/jobs` shows only in-flight jobs; `/jobs/history` exists server-side, unused by the UI | web UI | — |

### 9. A failed stage's error becomes the next stage's input

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

### 10. The MCP gateway drops `ImageContent`

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

### 7 & 8. The portal's OIDC client cannot be configured

`client_id` is read from `NEXT_PUBLIC_OIDC_CLIENT_ID`, which Next.js inlines at build time and which
is not inlined in the published artifact — it evaluates to `""` on every load against the browser
`process` polyfill. There is no runtime escape hatch in the static export, so the published wheel
cannot be pointed at any identity provider, and the failure is silent in both UI and server log.

**Fix direction (report 8, option 1):** serve `client_id` and `scope` from `/auth-info`, which
already carries `issuer` and `audience` and exists precisely so a client can render the right login
gate before it holds a token. That makes it workspace configuration, so one published wheel works
for every deployment. Plus: fail loudly when it is still empty; keep a token-entry affordance in
`jwt` mode (the bearer path already works, and today any breakage in the redirect flow locks the
portal completely); and move to a single `/auth/callback` redirect URI restoring the path from
`state`, since `origin + pathname` forces every IdP to be configured with a wildcard.

Worth documenting either way: serve takes identity from `sub` and matches it against `members:` in
`swarm/roles.yaml`. Most IdPs put a UUID in `sub`, which authenticates fine and then fails every
role check with a 403 indistinguishable from being unauthenticated.

### 11. `auth: none` cannot name its operator (feature request)

`NoneAuthProvider` hands out `client_id="anonymous"` with `scopes={"*"}` and an `authorize()` that
returns `True` unconditionally (verified). The approval engine does not consult scopes — it matches
`client_id` against `members:` in `swarm/roles.yaml`. So `anonymous` is refused not for lacking
authority but for being nobody: a mode that permits every action makes the one action depending on
identity impossible.

Consequence: a single operator on a laptop must stand up an IdP to click Approve on their own
machine, which is why the approval path in that workspace went unexercised — and why the
break-glass event route (the one that dropped comments until 1.137.0) gets used at all. **This is
the root cause behind the rework bug, not merely adjacent to it.**

**Fix direction:** optional `identity` / `identity_name` on `provider: none`, defaulting to today's
`anonymous` so nothing existing moves. Granting a name grants no capability that mode does not
already give. Guardrails worth shipping with it: keep the existing loopback-only refusal explicitly
covering the named case; record `provider="none"` in the audit so "verified by an IdP" stays
distinguishable from "someone on loopback claimed to be srijith"; and warn at startup when the
configured identity matches no role member — the same warning catches the `sub`-is-a-UUID mistake
under `jwt`.

## Fixed

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

And a testing rule, learned repeatedly: **these bugs are invisible to mocked tests.** The sequence
bug needed a real Postgres; the governance bug needed a real run. Unit tests are for coverage; a
live pipeline is for confidence.
