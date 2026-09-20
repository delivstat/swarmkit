# Per-caller credential delegation

How one agent, deployed once, serves a whole organisation against per-person services — each run
reaching the caller's own calendar, mailbox or repository, and no one else's.

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

## Goal

A credential may declare that its owner **is the run's authenticated caller**, resolved per run:

```yaml
credentials:
  google-calendar:
    source: oauth
    config:
      endpoint: https://mcp.example.com/google
      owner: caller          # not a literal identity — whoever authenticated this run
```

One workspace, one topology, one connection entry. Alice calls `POST /run/schedule-meeting` with her
bearer and the run reaches Alice's calendar; Bob's call reaches Bob's. Neither can reach the other's,
and a caller who has not connected is told to connect rather than handed a failure.

## Non-goals

- **Being an identity provider.** Unchanged from `mcp-oauth.md`. SwarmKit is an OAuth *client* on
  the outbound side and a resource server on the inbound side. These stay separate concerns that
  merely agree on a key.
- **OAuth token exchange / on-behalf-of (RFC 8693).** Tempting — exchange the inbound JWT for a
  downstream Google token and store nothing. It is out of scope because it requires a trust
  relationship SwarmKit does not own: the inbound IdP and the downstream provider must be federated,
  with SwarmKit registered as a permitted actor. That is true inside a single Entra or Okta tenant
  and false for the general case of "a JWT from our IdP, a calendar at Google." An org that *has*
  that federation should be able to use it later; the resolution seam below is where it would plug
  in, and nothing here forecloses it.
- **Per-request token pass-through.** The caller sending their own Google token on the request, for
  the runtime to relay. Rejected: it puts provider tokens on SwarmKit's API surface (against "no
  route returns a token", and now no route *accepts* one either), makes every caller responsible for
  refresh, and loses the audit property that a refresh is an act taken with a named identity.
- **Cross-instance token availability.** A token obtained on instance A is still not on instance B.
  Per-caller delegation makes that sharper, not different; it stays `mcp-oauth.md` open question 4
  and belongs to the control plane.
- **Changing today's behaviour.** A literal `owner:` and the sole-owner fallback keep working
  exactly as they do. This is opt-in.

## The security property, stated first

Everything below is in service of one invariant:

> **Delegation only ever narrows.** A run may use a token whose owner is the authenticated principal
> that started it. It may never resolve to any other owner, and `owner: caller` must never fall back
> to a different identity — including the sole-owner convenience that exists today.

The failure to avoid is an escalation disguised as a convenience: a topology that works in
development (one operator, sole-owner fallback) and, deployed with auth on, quietly serves every
caller from the developer's token. Therefore `owner: caller` with no resolvable principal is a
**refusal**, never a fallback. The two features must not compose.

## Resolution

A run-scoped principal, set where the run begins and read where the credential resolves. The
codebase already has this exact pattern three times — `_run_scope.py` (current run id),
`_stop_requests.py` (stop checker), `governance/_limits.py` (the circuit-breaker tracker installed by
`_begin_run`) — and this is a fourth of the same shape, not a new mechanism:

```python
# _principal.py
_current_principal: ContextVar[str | None] = ContextVar("swarmkit_principal", default=None)

def current_principal() -> str | None: ...
def begin_principal(client_id: str) -> Token[str | None]: ...
```

`serve` sets it from `request.state.identity.client_id` when it starts a job, and clears it when the
job ends. `CredentialService` gains one branch:

| `config.owner` | Resolves to |
|---|---|
| a literal identity | that owner — today's behaviour, unchanged |
| absent | the sole owner when there is exactly one — today's behaviour, unchanged |
| `caller` | `current_principal()`; **refuse** if unset |

The pre-run refresh pass ([mcp-oauth.md](mcp-oauth.md)) runs inside the same scope, so it refreshes
*the caller's* token for *this* run, not a designated one.

### Which string is the owner

This is the part that will bite, and it deserves to be decided rather than discovered.

The owner column holds whatever identity the portal recorded at login. For JWT auth the identity's
`client_id` is the token's `sub` — often an opaque GUID, not an email, while the examples in the
docs show `owner: srijith@delivstat.com`. If the login flow records one string and delegation looks
up another, every user connects successfully and every run then reports "no stored token."

Rule: **the owner key is the identity `client_id`, the same value `/whoami` returns, on both
paths** — the login that stores the token and the run that resolves it. A human-readable label is
metadata for display, never the key. Where an operator wants emails as keys, that is an auth-provider
configuration (`identity_claim: email`), applied once, before anyone connects — changing it later
orphans every stored token, and the runtime should say so rather than silently miss.

### Propagation

A principal is a property of the run tree, and the boundaries are not all the same:

- **Child runs and delegation within the instance** — propagate. A root agent delegating to a child
  topology is the same person's work; the ContextVar carries naturally.
- **Across A2A to a remote agent** — do **not** propagate. The far instance has its own identity
  domain and its own token store; a `sub` from our IdP means nothing there, and forwarding it would
  invite the remote side to act as a local owner. The remote call authenticates as itself
  (`credentials_ref` on the agent skill), exactly as today.
- **Triggers (cron, webhook)** — there is no human, so `owner: caller` refuses. A scheduled workflow
  that needs a person's data uses an explicitly designated unattended credential; that designation
  is already the rule in `mcp-oauth.md` ("an unattended credential is designated explicitly; it is
  never the accidental result of one person logging in").
- **CLI (`swarmkit run`)** — no authenticated caller. Refuse, naming the reason. (An operator who
  wants their own identity locally can set a literal owner.)

### Failure modes, all fail-closed

| Situation | Behaviour |
|---|---|
| `owner: caller`, principal set, token present | resolves; audit records credential, owner, principal, "caller" |
| `owner: caller`, principal set, **no token for them** | refuse with an actionable error naming the credential and the connect URL — this is a first-run state, not a fault |
| `owner: caller`, **no principal** (CLI, trigger, A2A, auth `none`) | refuse at run start, naming why; never fall back to sole-owner |
| `owner: caller`, auth mode is `none` | refused at **load**, not at run — a workspace that cannot ever resolve it is misconfigured, and validation should say so |
| mixed credentials in one topology | allowed; each resolves on its own rule |

The second row is the one that decides whether this feature is pleasant. A user's first call to a
shared agent should return "connect your calendar here," carrying the URL, and the portal should show
the same thing — the same shape as `ConsentRequired` parking, reached from the other end.

### Audit

`mcp-oauth.md` already holds that a refresh is "an action taken on a person's behalf, with their
identity." Per-caller resolution extends the record, never the exposure: every resolution writes the
credential id, the owner, the principal, and *how* the owner was chosen (`literal` / `sole` /
`caller`). Still no token in the log, still no route returning one. "How it was chosen" is the field
that makes an escalation visible after the fact.

## API and schema shape

Small, because the seam exists. `credential_ref.config` is already `additionalProperties: true`, so
the sentinel needs no structural schema change — only the `oauth` config contract and its
documentation, plus validation that rejects `owner: caller` when auth is `none`. Per
[schema-change-discipline.md](../../docs/notes/schema-change-discipline.md), the `$comment` on
`credential_ref` and `reference/connections.md` move together.

Reads that change:

| Route | Change |
|---|---|
| `GET /api/oauth/credentials` | already per-owner metadata; gains a per-caller view — *is there a token for me* — so a portal can render "Connect" for the signed-in person |
| `GET /whoami` | unchanged; becomes load-bearing, and should be documented as the key delegation matches on |

Nothing new returns a token.

## Portal changes

The portal is not optional here, and it is not only a new page: **an existing invariant is compiled
into the UI's own logic.** `packages/ui/lib/connections.ts` says so in its header — *"its credential
has one owner fixed at setup"* — and `statusFor()` returns one `ConnectionStatus` per row for the
whole workspace (`needs-credential`, `unresolved`, `no-auth`, ready). That is exactly the assumption
this note removes.

**1. Status becomes per-viewer.** With `owner: caller`, "does this credential resolve" has no single
answer: the same row is ready for Alice and not-yet-connected for Bob. `statusFor()` takes the
signed-in identity, and `ConnectionStatus` gains a state for *you have not connected this one*,
distinct from `needs-credential` (the workspace is misconfigured) and `unresolved` (configured but
the source returned nothing). Those three must not be collapsed: one is the operator's bug, one is
the operator's environment, one is the viewer's to fix and is a perfectly healthy state.

**2. Connect changes meaning, so it must change wording.** Today the Connect button
(`oauthProbe` → `oauthLogin` → popup → callback) stores a token owned by whoever clicked it, which in
a single-operator workspace is effectively the workspace's. Under `owner: caller` the same click
connects *only that person's* account. An operator must not be able to read the button as "set this
up for everybody" — that misreading is how one person's token silently becomes the team's.

**3. A non-operator view.** Most people who need to connect an account have no business seeing the
operator inventory — adding credentials, editing `workspace.yaml` through
`PUT /api/workspace/config/...`. They need one screen: *the accounts this agent will use as me*, each
with its state and a Connect button. Whether that is the Connections page filtered by scope or a
separate route is an implementation choice; that it is scope-gated is not.

**4. Listing must not leak the roster.** `GET /api/oauth/credentials` returns metadata for every
owner — provider, owner, expiry, scopes. In a single-operator workspace that is an inventory; in a
multi-user deployment it is a list of who uses what, which is not a non-admin's business. The
per-caller view needs *is there a token for me*, and the full listing stays admin-scoped. This is a
genuine authorization change, not a display tweak, and it is the one piece of this note that is a
privacy regression if it ships thoughtlessly.

None of this needs new UI machinery: the page, the API client methods (`oauthProbe`, `oauthLogin`,
`oauthDisconnect`), and a vitest suite for the logic (`packages/ui/lib/connections.test.ts`) all
exist.

## Test plan

The point of writing this section against the real harnesses is that **the security property is
cheap to test today** — the scaffolding it needs is already in the repo, so there is no excuse for
asserting it in prose instead of in code.

**Unit — the resolution matrix (`tests/test_credential_service.py`).** That file already builds a
`TokenStore(tmp_path)`, calls `store.save(..., owner="srijith")`, constructs
`CredentialService(tmp_path, {...})` and awaits `service.resolve(name)`. Every row of the table above
is that same setup with a second owner added:

| Case | Expected |
|---|---|
| literal `owner` | that owner's token (regression — unchanged behaviour) |
| no `owner`, exactly one stored | sole owner (regression — unchanged behaviour) |
| `owner: caller`, principal Alice, Alice has a token | Alice's token |
| **`owner: caller`, principal Alice, Bob is the *sole* stored owner** | **raises — no fallback** |
| `owner: caller`, no principal | raises, naming why |
| `owner: caller`, principal Alice, no token for Alice | actionable "connect" error carrying the URL |

Row four is the feature. If only one test from this note survives, it is that one: it is the
difference between delegation and privilege escalation, and it fails loudly the day someone
"simplifies" the resolver by reusing the sole-owner convenience.

**Unit — principal isolation.** Two `asyncio.gather`-ed resolutions with different principals in one
process each get their own token. This is why the principal is a `ContextVar` and not a module
global, and it is the same property `_run_scope.py` already relies on.

**Unit — the owner-key agreement.** The bug this prevents is the silent one: the login stores under
one string, the run looks up another, every user connects and nothing resolves. `tests/test_jwt_auth.py`
already mints RS256 tokens against an in-test RSA key with a mocked JWKS client, so the test is
cheap: assert that the identity `/whoami` reports and the owner the resolver looks up are **the same
string**, for JWT (`sub`), for api_key, and for `none` (the named operator,
`tests/test_none_auth_identity.py`).

**Integration — isolation, end to end.** One `create_app` workspace, one topology, two JWTs minted by
the existing helper, and a stub MCP server that reports *which account it was called as*. Alice's run
touches Alice's account and Bob's touches Bob's — asserted from the stub's per-account data, **not by
mocking the resolver**, because a test that mocks the thing under test proves only that the mock was
called.

**Integration — the first-run path.** Bob calls before connecting, receives the connect response,
connects through the stub provider, and the identical call then succeeds **with no edit to
`workspace.yaml`**. That "no edit" is the whole product claim: the agent is deployed once.

**Integration — boundaries.** A child run inherits the principal; an A2A call does not forward it; a
cron trigger on the same topology refuses; `owner: caller` under auth `none` is refused at load by
`swarmkit validate`, not at run time.

**Integration — refresh.** The pre-run refresh pass refreshes *the caller's* token
(`tests/test_oauth_refresh.py` has the expiry-window arithmetic and the revoked-refresh case to
extend).

**Security.** No route returns a token (existing guarantee, re-asserted for the new per-caller read);
the non-admin per-caller listing does not disclose other owners; the audit row names principal, owner
and *how the owner was chosen*.

**TypeScript (`packages/ui/lib/connections.test.ts`).** `statusFor()` returns a different status for
the same row under two viewers; the three "not ready" states stay distinct; a non-operator sees no
other owner.

**What this plan does not cover, honestly.** A stub provider proves the runtime's contract and
nothing about a real one. Consent screens, scope drift between what was requested and what was
granted, refresh-token lifetimes and admin-consent policies differ per provider (Google, Entra,
Okta), and they surface only in a manual pass against a real tenant. That pass should happen once
before this is called done, and its findings belong in this note — not in a test that cannot run in
CI.

## Demo plan

`just demo-per-caller-credentials`, against the stub provider from the `mcp-oauth` demo: one
`swarmkit serve`, one topology, two JWTs. Alice runs it and sees Alice's events; Bob runs the same
route and sees Bob's; Bob, before connecting, gets "connect your calendar" with a link that works.

**The isolation is the demo.** Anyone can show a login working. The claim worth demonstrating is
that the second user changes nothing about the first — and that the developer's own token, sitting
in the same store, is not what either of them got.

## Open questions

1. **One page or two?** The portal changes are settled above except their shape: whether the
   non-operator view is the Connections page filtered by scope or a separate lightweight route. A
   filtered page is less code and one less thing to keep in step; a separate route is harder to leak
   the operator inventory from by accident. Leaning separate route, for the same reason the listing
   split is a privacy question rather than a display one.
2. **Revocation at the organisation level.** An employee leaves; their rows should go. Deleting by
   owner exists (`DELETE /api/oauth/credentials/{id}` is owner-scoped), but nothing sweeps by
   identity. Probably a control-plane concern, like question 4 of `mcp-oauth.md`.
3. **Does `owner: caller` belong on the credential or on the agent?** On the credential here, because
   the credential is what has an owner. An argument exists for the agent — "this agent always acts as
   its caller" — which would read better in a topology but splits the fact across two artifacts.
4. **Federated token exchange as a later `owner:` mode.** For a single-tenant Entra/Okta deployment,
   `owner: exchange` could obtain a downstream token from the inbound assertion and store nothing.
   The resolution table is the right place for it; whether the audit and refresh stories survive
   statelessness needs its own note.
