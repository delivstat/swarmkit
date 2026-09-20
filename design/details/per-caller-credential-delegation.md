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

## Test plan

- **Unit** — the resolution matrix above, every row, including that `owner: caller` with no principal
  raises rather than falling back when a sole owner exists. That single test is the feature's
  security property.
- **Unit** — principal scoping: concurrent jobs in one process resolve their own principals (the
  reason this is a `ContextVar` and not a global, same as the run id).
- **Security** — a run started by Alice cannot resolve Bob's token by any configuration reachable
  from the topology; no route returns a token; the audit row names principal and choice.
- **Integration** — two identities, one workspace, one topology, a stub OAuth provider: each caller's
  run touches only their own account, proven by the stub's per-account data rather than by assertion.
- **Integration** — Bob calls before connecting and receives the actionable connect response, then
  connects and the same call succeeds, with no change to the workspace.
- **Integration** — propagation: a child run sees the principal; an A2A call does not; a cron trigger
  on the same topology refuses.
- **Integration** — pre-run refresh refreshes the caller's token, not the designated one.

## Demo plan

`just demo-per-caller-credentials`, against the stub provider from the `mcp-oauth` demo: one
`swarmkit serve`, one topology, two JWTs. Alice runs it and sees Alice's events; Bob runs the same
route and sees Bob's; Bob, before connecting, gets "connect your calendar" with a link that works.

**The isolation is the demo.** Anyone can show a login working. The claim worth demonstrating is
that the second user changes nothing about the first — and that the developer's own token, sitting
in the same store, is not what either of them got.

## Open questions

1. **Portal ergonomics for a non-operator.** A user who is not the workspace owner needs a page that
   does exactly one thing: connect the accounts this agent will use as me. That is a different view
   from today's Connections page, which is an operator's inventory.
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
