# Per-caller credential delegation

How one agent, deployed once, serves a whole organisation against per-person services — each run
reaching the caller's own calendar, mailbox or repository, and no one else's — without disturbing the
connections that are meant to be shared.

Builds directly on [mcp-oauth.md](mcp-oauth.md) (per-owner token storage, PKCE login, pre-run
refresh) and [credential-service.md](credential-service.md) (one resolution path for every entry
point). Read those first; this note changes exactly one thing in them.

## The thing that is missing

Everything needed for a multi-user agent already exists except the last join.

Tokens are already per-person: the OAuth store is keyed `(credential_id, owner)`, one row per pair,
and the owner is the authenticated identity `serve` resolves. The inbound side already
authenticates: JWT/JWKS or API keys produce an identity (`/whoami` → `client_id`, for JWT the
token's `sub`). Refresh already happens before a run, headless.

What does not exist is the binding between them. Credential resolution reads the owner from
configuration:

```python
owner = str(config.get("owner", "")) or _sole_owner(self.token_store, name)
```

The owner is a fact written at setup, not a property of the run. `mcp-oauth.md` settled this
deliberately (open question 5): *"one owner per connection, fixed at setup… Every run through a
connection uses that connection's credential, whoever or whatever started it."* The alternative —
*"an interactive run acts as whoever started it"* — was named and scoped out, "out of scope until
the fleet needs it."

That was the right call for a single-operator workspace. It is the wrong one the moment a workspace
is deployed for a team, because the only way to express "Alice's calendar" today is a connection
whose owner is literally Alice, and therefore a topology per person. Ten people means ten
connections and ten topologies that differ by one string.

## Goal: two kinds of connection, declared

Not every connection wants a person behind it, and most do not. A GitHub machine token, a fileshare
credential, an internal API key — these are set up once and every run uses the same one, correctly.
A calendar or a mailbox is the opposite: the whole point is *whose*.

So a connection declares which kind it is:

```yaml
credentials:
  # Global — set up once, every run uses it. The default.
  github:
    source: env
    config: { env: GITHUB_TOKEN }

  fileshare:
    source: file
    identity: global            # explicit; identical to omitting it
    config: { path: /etc/smb.cred }

  # Per-user — the run uses the token belonging to whoever called it.
  google-calendar:
    source: oauth
    identity: per-user
    config: { endpoint: https://mcp.example.com/google }
```

`identity: global` is the **default**, and that matters for more than compatibility: `serve` can run
with `auth: none`, the CLI has no caller at all, and a trigger has no human. A default of `per-user`
would make the common deployment un-runnable and would push people toward turning it off. The
direction that needs a deliberate act is the one with an identity in it.

One topology mixes both freely: a code-review swarm reads GitHub through a shared machine token and
files the outcome in the reviewer's own calendar. That is the realistic shape, and it falls out of
putting the mode on the connection rather than on the agent or the topology.

### Why not `scope:`

`scope` is already the word for what an OAuth provider granted — `mcp-oauth.md` and the portal both
use it that way, and `GET /api/oauth/credentials` returns it per token. A second `scope` meaning
"shared or personal" in the same YAML, two lines from the first, is a collision that will be misread
for as long as the field exists. `identity` says whose the connection is, which is the actual
question.

## Non-goals

- **Being an identity provider.** Unchanged from `mcp-oauth.md`. SwarmKit is an OAuth *client* on
  the outbound side and a resource server on the inbound side. These stay separate concerns that
  merely agree on a key.
- **OAuth token exchange / on-behalf-of (RFC 8693).** Tempting — exchange the inbound JWT for a
  downstream token and store nothing. Out of scope because it requires a trust relationship SwarmKit
  does not own: the inbound IdP and the downstream provider must be federated, with SwarmKit
  registered as a permitted actor. True inside a single Entra or Okta tenant, false for the general
  case of "a JWT from our IdP, a calendar at Google." An org that *has* that federation should be
  able to use it later; it is a third `identity:` value, and nothing here forecloses it.
- **Per-request token pass-through.** The caller sending their own provider token on the request for
  the runtime to relay. Rejected: it puts provider tokens on SwarmKit's API surface (against "no
  route returns a token", and now no route *accepts* one either), makes every caller responsible for
  refresh, and loses the audit property that a refresh is an act taken with a named identity.
- **Cross-instance token availability.** A token obtained on instance A is still not on instance B.
  Per-user connections make that sharper, not different; it stays `mcp-oauth.md` open question 4 and
  belongs to the control plane.
- **Changing what existing workspaces do.** Every connection today is `identity: global` by
  omission, and global resolution is byte-for-byte today's behaviour. There is no migration.

## The security property, stated first

Two modes make this cleaner than a single magic owner value would, because **sharing becomes a
declaration rather than an emergent fallback**:

> A `per-user` connection resolves to the authenticated caller's own token, or it **refuses**. It
> never falls back to a designated owner, to the sole stored owner, or to a global connection.
>
> A `global` connection is shared by every run — and is shared *because someone wrote that down*,
> not because a fallback fired.

The failure this forecloses is an escalation disguised as a convenience. Today's sole-owner
convenience — "no owner named, exactly one stored, use it" — is a kindness in a single-operator
workspace and a disaster in a multi-user one: a topology that works in development would, deployed
with auth on, quietly serve every caller from the developer's token. Under two modes that
convenience is confined to `global`, where being shared is the declared intent, and it does not
exist in `per-user` at all. The two must never compose.

## Resolution

A run-scoped principal, set where the run begins and read where the credential resolves. The
codebase already has this pattern three times — `_run_scope.py` (current run id), `_stop_requests.py`
(stop checker), `governance/_limits.py` (the circuit-breaker tracker installed by `_begin_run`) — and
this is a fourth of the same shape, not a new mechanism:

```python
# _principal.py
_current_principal: ContextVar[str | None] = ContextVar("swarmkit_principal", default=None)

def current_principal() -> str | None: ...
def begin_principal(client_id: str) -> Token[str | None]: ...
```

`serve` sets it from `request.state.identity.client_id` when it starts a job and clears it when the
job ends. `CredentialService` then branches on the declared mode, not on a sentinel buried in
`config`:

| `identity` | `config.owner` | Resolves to |
|---|---|---|
| `global` (default) | a literal identity | that owner — today's behaviour, unchanged |
| `global` | absent | the sole owner when there is exactly one — today's behaviour, unchanged |
| `per-user` | *forbidden* | `current_principal()`'s own token; **refuse** if there is no principal, and **never** fall back |

The pre-run refresh pass runs inside the same scope, so a per-user connection refreshes *the
caller's* token for *this* run rather than a designated one.

### Which sources can be per-user

`identity: per-user` is only meaningful where the secret can be keyed by owner. `oauth` can, because
the store already is. `env` and `file` cannot — an environment variable is process-wide, and a file
is the same bytes for everyone. Declaring `per-user` on those is a **load-time refusal**, not a
runtime surprise.

The user's "oauth or otherwise" is the right instinct, so this is a provider property rather than a
hard-coded list: a `SecretsProvider` declares `supports_per_user`, and validation reads it. A future
vault provider with a per-owner path template (`secret/users/{owner}/calendar`) becomes per-user
capable without touching the resolver.

### Validation, at load

A workspace that cannot possibly work should fail `swarmkit validate`, not a run at minute eight:

- `identity: per-user` on a source whose provider does not support per-user keying → error naming the
  source.
- `identity: per-user` together with a literal `config.owner` → error; they contradict each other,
  and guessing which wins is how escalations happen.
- `identity: per-user` while `server.auth.provider` is `none` → error. There is no caller to be, so
  the connection can never resolve. This is the check that keeps the permissive default honest.

### Which string is the owner

This is the part that will bite, and it deserves to be decided rather than discovered.

The owner column holds whatever identity the portal recorded at login. For JWT auth the identity's
`client_id` is the token's `sub` — often an opaque GUID, not an email, while the examples in the docs
show `owner: srijith@delivstat.com`. If the login flow records one string and delegation looks up
another, every user connects successfully and every run then reports "no stored token."

Rule: **the owner key is the identity `client_id`, the same value `/whoami` returns, on both paths** —
the login that stores the token and the run that resolves it. A human-readable label is metadata for
display, never the key. Where an operator wants emails as keys, that is an auth-provider
configuration (`identity_claim: email`), applied once, before anyone connects — changing it later
orphans every stored token, and the runtime should say so rather than silently miss.

### Propagation

A principal is a property of the run tree, and the boundaries are not all the same:

- **Child runs and delegation within the instance** — propagate. A root agent delegating to a child
  topology is the same person's work; the `ContextVar` carries naturally.
- **Across A2A to a remote agent** — do **not** propagate. The far instance has its own identity
  domain and its own token store; a `sub` from our IdP means nothing there, and forwarding it would
  invite the remote side to act as a local owner. The remote call authenticates as itself
  (`credentials_ref` on the agent skill), exactly as today.
- **Triggers (cron, webhook)** — no human, so a per-user connection refuses. A scheduled workflow
  that needs a person's data uses a `global` connection with a designated owner, which is precisely
  what `mcp-oauth.md` means by "an unattended credential is designated explicitly; it is never the
  accidental result of one person logging in."
- **CLI (`swarmkit run`)** — no authenticated caller. Refuse, naming the reason.

### Failure modes, all fail-closed

| Situation | Behaviour |
|---|---|
| `global`, any caller | resolves as today; audit records the designated owner |
| `per-user`, principal set, token present | resolves; audit records credential, owner, principal, `per-user` |
| `per-user`, principal set, **no token for them** | refuse with an actionable error naming the credential and the connect URL — a first-run state, not a fault |
| `per-user`, **no principal** (CLI, trigger, A2A) | refuse at run start, naming why; never fall back |
| `per-user`, auth `none` | refused at **load** — see validation above |
| mixed connections in one topology | each resolves on its own mode |

The third row decides whether this feature is pleasant. A user's first call to a shared agent should
return "connect your calendar here," carrying the URL, and the portal should show the same thing —
the same shape as `ConsentRequired` parking, reached from the other end.

## Dispatch: the call the tool actually makes

"Based on the connection type the call should be made" is where this stops being a resolver change,
and it is the most likely place to ship a security bug.

`mcp/_client.py` today: *"Manages MCP server connections. One session per server, lazily started."*
`self._sessions` is keyed by **server id alone**, and `self._session_credentials` records "what secret
each open session was opened with, so a changed one can be detected." For a shared connection that is
exactly right. For a per-user one it is wrong in three ways at once:

1. **Correctness and isolation.** Alice's run opens the session carrying Alice's bearer; Bob's run
   finds an open session for that server id and reuses it. Bob now acts as Alice. Nothing in the
   resolver prevents this, because the resolver already returned the right token — the leak is one
   layer down, in session reuse.
2. **Thrash.** The existing changed-secret detection would notice and reopen on every alternating
   caller, turning two concurrent users into a reconnect loop.
3. **Eager warm-up.** `start_all` opens every configured server's session up front. There is no
   caller at warm-up, so a per-user server simply cannot be pre-opened.

So the session key follows the connection's mode: **`server_id` for `global`, `(server_id, owner)` for
`per-user`.** Global keeps its single warm session and its changed-secret detection unchanged.
Per-user sessions are opened lazily on first use by that owner, are never handed to another owner,
and need a lifetime policy — idle eviction and a ceiling — because the population is now users ×
servers rather than servers. A per-user session must not outlive its usefulness merely because one
person ran something once.

The same rule reaches the other governed dispatchers (`commands/_governed.py`,
`agent_skill/_governed.py`): anything that caches a connection keyed by configuration alone has to
include the owner when the connection is per-user.

### Audit

`mcp-oauth.md` already holds that a refresh is "an action taken on a person's behalf, with their
identity." Per-user resolution extends the record, never the exposure: every resolution writes the
credential id, the owner, the principal, and the **mode** (`global` / `per-user`). Still no token in
the log, still no route returning one. The mode is the field that makes an escalation visible after
the fact — a `global` row where a reviewer expected `per-user` is the whole finding.

## Schema and API shape

`identity` is a new top-level key on `credential_ref`, beside `source` — not inside `config`, because
it is not provider-specific configuration; it is what the connection *is*. `credential_ref` is
`additionalProperties: false`, so this is an additive schema change, and per
[schema-change-discipline.md](../../docs/notes/schema-change-discipline.md) the schema, its
`$comment`, the TS types and `reference/connections.md` move together.

```yaml
identity: global | per-user      # default: global
```

Reads that change:

| Route | Change |
|---|---|
| `GET /api/oauth/credentials` | gains the mode per entry, and a per-caller view — *is there a token for me* — so a portal can render Connect for the signed-in person |
| `GET /whoami` | unchanged; becomes load-bearing, and should be documented as the key delegation matches on |

Nothing new returns a token.

## Portal changes

The portal is not optional here, and an existing invariant is compiled into its logic:
`packages/ui/lib/connections.ts` says so in its own header — *"its credential has one owner fixed at
setup"* — and `statusFor()` returns one `ConnectionStatus` per row for the whole workspace.

Two modes make the UI simpler than a single dynamic owner would have:

**1. Mode is the primary thing a row shows.** Global versus per-user is what an operator most needs
to see at a glance, because it is the difference between "I set this up for everyone" and "each
person sets this up." It belongs next to the name, not buried in a detail panel.

**2. Only per-user rows are per-viewer.** A global row keeps exactly today's single status —
`needs-credential`, `unresolved`, `no-auth`, ready — computed once for the workspace. A per-user row
has a status *for the signed-in person*, and needs a state meaning *you have not connected this one*,
distinct from `needs-credential` (the operator's bug) and `unresolved` (the operator's environment).
Those three must not collapse: one is a misconfiguration, one is an environment fault, and one is the
viewer's to fix and a perfectly healthy state.

**3. Connect changes meaning on per-user rows, so it must change wording.** Today's button
(`oauthProbe` → `oauthLogin` → popup → callback) stores a token owned by whoever clicked it, which in
a single-operator workspace is effectively the workspace's. On a per-user row the same click connects
*only that person's* account. An operator must not be able to read it as "set this up for everybody."

**4. A scope-gated non-operator view.** Most people who need to connect an account have no business
in the operator inventory — adding credentials, editing `workspace.yaml` through
`PUT /api/workspace/config/...`. They need one screen: the accounts this agent will use as me, each
with its state and a Connect button.

**5. Listing must not leak the roster.** `GET /api/oauth/credentials` returns metadata for every
owner — provider, owner, expiry, scopes. In a single-operator workspace that is an inventory; in a
multi-user deployment it is a list of who uses what, which is not a non-admin's business. The
per-caller view needs *is there a token for me*; the full listing stays admin-scoped. This is a
genuine authorization change, not a display tweak, and it is the one piece of this note that is a
privacy regression if it ships thoughtlessly.

No new UI machinery is needed: the page, the API client methods (`oauthProbe`, `oauthLogin`,
`oauthDisconnect`) and a vitest suite for the logic (`packages/ui/lib/connections.test.ts`) all exist.

## Test plan

The point of writing this against the real harnesses is that **the security property is cheap to test
today** — the scaffolding is already in the repo, so there is no excuse for asserting it in prose
instead of in code.

**Unit — the resolution matrix (`tests/test_credential_service.py`).** That file already builds a
`TokenStore(tmp_path)`, calls `store.save(..., owner="srijith")`, constructs
`CredentialService(tmp_path, {...})` and awaits `service.resolve(name)`. Every row is that same setup
with a second owner and a mode:

| Case | Expected |
|---|---|
| `global`, literal owner | that owner's token (regression — unchanged) |
| `global`, no owner, exactly one stored | sole owner (regression — unchanged) |
| `per-user`, principal Alice, Alice has a token | Alice's token |
| **`per-user`, principal Alice, Bob is the *sole* stored owner** | **raises — no fallback** |
| **`per-user`, principal Alice, a `global` connection to the same service exists** | **raises — no cross-mode fallback** |
| `per-user`, no principal | raises, naming why |
| `per-user`, principal Alice, no token for Alice | actionable "connect" error carrying the URL |

Rows four and five are the feature. If only two tests from this note survive, they are those: they are
the difference between delegation and privilege escalation, and they fail loudly the day someone
"simplifies" the resolver by reusing the sole-owner convenience.

**Unit — validation at load.** Per-user on `env`/`file`; per-user with a literal owner; per-user under
auth `none`. Each is an error naming the cause, asserted through `swarmkit validate`, not a run.

**Unit — principal isolation.** Two `asyncio.gather`-ed resolutions with different principals in one
process each get their own token. This is why the principal is a `ContextVar` and not a module global,
the same property `_run_scope.py` relies on.

**Unit — the owner-key agreement.** The bug this prevents is the silent one: login stores under one
string, the run looks up another, every user connects and nothing resolves. `tests/test_jwt_auth.py`
already mints RS256 tokens against an in-test RSA key with a mocked JWKS client, so asserting that the
identity `/whoami` reports and the owner the resolver looks up are **the same string** is cheap — for
JWT (`sub`), for api_key, and for `none` (`tests/test_none_auth_identity.py`).

**Unit — session keying.** Two owners, one per-user server: two sessions, and neither is handed the
other's. One owner, one global server: one session, reused, with changed-secret detection intact. This
is the test that would have caught the leak described in *Dispatch*, and it belongs beside
`mcp/_client.py`'s existing session tests rather than in the integration tier, because it is a keying
bug, not a wiring one.

**Integration — isolation, end to end.** One `create_app` workspace, one topology, two JWTs from the
existing helper, and a stub MCP server that reports *which account it was called as*. Alice's run
touches Alice's account and Bob's touches Bob's — asserted from the stub's per-account data, **not by
mocking the resolver**, because a test that mocks the thing under test proves only that the mock was
called. The same workspace carries a `global` connection alongside, and both callers reach the same
shared account through it — mixing is the realistic case and should be proven, not assumed.

**Integration — the first-run path.** Bob calls before connecting, receives the connect response,
connects through the stub provider, and the identical call then succeeds **with no edit to
`workspace.yaml`**. That "no edit" is the whole product claim: the agent is deployed once.

**Integration — boundaries.** A child run inherits the principal; an A2A call does not forward it; a
cron trigger on a per-user connection refuses while the same trigger on a global one succeeds.

**Integration — refresh.** The pre-run refresh refreshes *the caller's* token for a per-user
connection and the designated one for a global connection (`tests/test_oauth_refresh.py` has the
expiry-window arithmetic and the revoked-refresh case to extend).

**Security.** No route returns a token (existing guarantee, re-asserted for the new per-caller read);
the non-admin per-caller listing does not disclose other owners; the audit row names principal, owner
and mode.

**TypeScript (`packages/ui/lib/connections.test.ts`).** A global row's status is identical for two
viewers; a per-user row's differs; the three "not ready" states stay distinct; a non-operator sees no
other owner.

**What this plan does not cover, honestly.** A stub provider proves the runtime's contract and nothing
about a real one. Consent screens, scope drift between what was requested and what was granted,
refresh-token lifetimes and admin-consent policies differ per provider (Google, Entra, Okta) and
surface only in a manual pass against a real tenant. That pass should happen once before this is
called done, and its findings belong in this note — not in a test that cannot run in CI.

## Demo plan

`just demo-per-caller-credentials`, against the stub provider from the `mcp-oauth` demo: one
`swarmkit serve`, one topology, two JWTs, and two connections — a global one and a per-user one.
Alice runs it and sees Alice's events; Bob runs the same route and sees Bob's; both reach the same
shared resource through the global connection; Bob, before connecting, gets "connect your calendar"
with a link that works.

**The isolation is the demo.** Anyone can show a login working. The claim worth demonstrating is that
the second user changes nothing about the first — and that the developer's own token, sitting in the
same store, is not what either of them got.

## Open questions

1. **One page or two?** The portal changes are settled above except their shape: whether the
   non-operator view is the Connections page filtered by scope or a separate lightweight route. A
   filtered page is less code and one less thing to keep in step; a separate route is harder to leak
   the operator inventory from by accident. Leaning separate route, for the same reason the listing
   split is a privacy question rather than a display one.
2. **Per-user session lifetime.** Idle eviction and a ceiling are clearly needed once sessions are
   users × servers, but the numbers are guesses until something real runs. A conservative idle TTL is
   the obvious start; a ceiling that closes the least-recently-used is the obvious companion.
3. **Revocation at the organisation level.** An employee leaves; their rows should go. Deleting by
   owner exists (`DELETE /api/oauth/credentials/{id}` is owner-scoped), but nothing sweeps by
   identity. Probably a control-plane concern, like question 4 of `mcp-oauth.md`.
4. **Federated token exchange as a third mode.** For a single-tenant Entra/Okta deployment,
   `identity: exchange` could obtain a downstream token from the inbound assertion and store nothing.
   The resolution table is the right place for it; whether the audit and refresh stories survive
   statelessness needs its own note.
