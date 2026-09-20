# Connections: credentials, remote MCP servers and OAuth

How a swarm reaches a service that needs a secret — and, for remote MCP servers that speak OAuth,
how a person logs in once from the portal and runs keep working afterwards. The design is in
[credential-service.md](https://github.com/delivstat/swarmkit/blob/main/design/details/credential-service.md)
and [mcp-oauth.md](https://github.com/delivstat/swarmkit/blob/main/design/details/mcp-oauth.md).

## One credential service, every entry point

A `credentials` entry in `workspace.yaml` is a **reference**, never a literal:

```yaml
credentials:
  github:
    source: env
    config: { env: GITHUB_TOKEN }
  linear:
    source: oauth
    config: { endpoint: https://mcp.linear.app/mcp }

mcp_servers:
  - id: linear
    transport: http
    endpoint: https://mcp.linear.app/mcp
    credentials_ref: linear
```

Every entry point — `swarmkit run`, `swarmkit serve`, the MCP client, a command pack's environment
— resolves a reference through the **same `CredentialService`**, at the point of use. That is the
whole reason it is a service: before it, each entry point assembled its own resolution and a
credential declared in YAML could fail to reach the server it was declared for.

| `source` | Resolves to | Notes |
|---|---|---|
| `env` | the named environment variable | `config.env` |
| `file` | the file's contents | `config.path` |
| `oauth` | a token from the runtime's encrypted store | obtained by logging in from the portal; refreshed automatically — below |
| `hashicorp-vault`, `aws-secrets-manager`, `gcp-secret-manager`, `azure-key-vault`, `plugin` | — | accepted by the schema, **refused at resolution** with a message naming the missing `SecretsProvider`. Declaring one does not make it work. |

A resolved secret reaches an MCP server as `Authorization: Bearer <token>` on an `http` transport, or
through `env`/`headers` templates (`{credential.<ref>}`) where the server wants it somewhere else.
Values are never written to the audit log or returned over HTTP.

## Logging in to a remote MCP server

For a server that speaks OAuth (the MCP authorization spec), the portal's **Connections** page does
the flow:

1. Add the server (`transport: http`, its endpoint) and a credential with `source: oauth` — from the
   Connections page or by editing `workspace.yaml`; the portal writes the same file
   (`PUT /api/workspace/config/{section}/{entry_id}`).
2. `GET /auth/mcp/probe?endpoint=…` asks the server whether it speaks OAuth and where its
   authorization server is.
3. **Connect** (`POST /api/oauth/login`) discovers the provider's metadata, registers SwarmKit as a
   client dynamically when the provider allows it (otherwise pass a `client_id` you registered), and
   opens the provider's login page in a popup with a PKCE challenge.
4. The provider sends the browser back to `GET /auth/mcp/callback`; the runtime exchanges the code
   for tokens and stores them.

What is stored, and where: tokens live in `.swarmkit/state/oauth.db`, **encrypted** with a key from
`SWARMKIT_OAUTH_KEY` or, when that is unset, one generated into `.swarmkit/oauth.key` on first use
(back it up: losing it means logging in again). `GET /api/oauth/credentials` lists **metadata only**
— provider, owner, expiry, scopes, whether a refresh token exists. **No endpoint returns a token.**

## Whose token it is

A token obtained in a browser belongs to **the person who logged in** — the authenticated identity
`serve` already resolves (`GET /whoami`). Tokens are keyed by `(credential, owner)`, so one person's
GitHub access does not silently become the workspace's.

Which owner a *run* uses is declared on the credential, with `identity`:

```yaml
credentials:
  # Global — set up once, used by every run. The default.
  github:
    source: env
    config: { env: GITHUB_TOKEN }

  # Per-user — each run uses the token of whoever authenticated it.
  google-calendar:
    source: oauth
    identity: per-user
    config: { endpoint: https://mcp.example.com/google }
```

| `identity` | Which token a run uses |
|---|---|
| `global` (default) | One connection for everyone. `config.owner` names whose token; with no owner named, the sole logged-in owner is used, and with several `config.owner` must say which. |
| `per-user` | The token belonging to the **authenticated caller of that run**. One deployed agent therefore serves a whole organisation, each person reaching their own calendar or mailbox. |

`global` is the default because it has to be: `serve` can run with `auth: none`, the CLI has no
caller, and a trigger has no human. A per-user default would make the ordinary deployment
un-runnable. One workspace mixes both freely — a shared machine token for GitHub, a personal
connection for a calendar.

**`per-user` never falls back.** If the caller has no token of their own, the run is refused with a
message telling them to connect — it does not quietly use a designated owner's token, or the sole
stored one. That refusal is the point: without it, a workspace that worked in development would,
deployed with auth on, serve every caller from the developer's token. It also requires a source that
can key a secret by owner (today `oauth`; `env` and `file` are the same bytes for everyone), an
authenticated caller, and no literal `config.owner` — which the schema refuses outright, since
naming an owner contradicts deriving one.

**If you connected but runs still say you have no token**, the identity recorded at login differs
from the one your API token presents — some providers consent as an opaque `sub` while their JWT
carries `email`. Compare `GET /whoami` with what the Connections page shows; the operator's log says
whether other identities are stored. Changing which claim identifies a user after people have
connected orphans their tokens.

Per-user connections keep one MCP session **per caller** rather than one per server, so nobody is
ever handed somebody else's open session. Those sessions are bounded and idle-swept —
`SWARMKIT_PER_USER_SESSION_MAX` (default 64), `SWARMKIT_PER_USER_STDIO_SESSION_MAX` (default 8, as
a per-user `stdio` server is a subprocess per user) and `SWARMKIT_PER_USER_SESSION_IDLE_S` (default
900). Global connections keep their single warm session, unchanged.

Design: [per-caller-credential-delegation.md](https://github.com/delivstat/swarmkit/blob/main/design/details/per-caller-credential-delegation.md).

## Refresh happens before a run, not during one

A run that would fail at minute eight because a token expired at minute three should have been
dealt with at minute zero. At run start the runtime refreshes every OAuth credential the topology
may use whose access token would expire inside the run's window — `SWARMKIT_OAUTH_RUN_WINDOW_S`,
900 s by default — silently, in one round trip, before the run makes many.

A refresh the provider refuses is **`ConsentRequired`**: the refresh token was revoked, expired or
its scope changed, and only a person in a browser can fix it. It is not retried; the run fails
naming the credential and the owner. The runtime can also tell which refresh tokens are nearing
their own end (`swarmkit_runtime.oauth.expiring_soon`: a week out, and a day out) — detection exists;
**nothing announces it yet**, so a scheduled run whose refresh token has lapsed fails with
`ConsentRequired` at its start, and someone logs in again.

## Forgetting a token

`DELETE /api/oauth/credentials/{credential_id}` removes the stored token for the caller as its owner
and revokes it upstream where the provider supports revocation. The store is keyed by credential id
and owner, independently of `workspace.yaml`: edit the entry and the token stays; delete the token
and the entry stays.

## Remote agents (A2A)

A remote agent is not a server entry: it is an `agent` skill with a `card_url`
([skills](skills.md#another-agent-as-a-skill-implementationtype-agent)). The Connections page lists
them next to servers and sinks with the same status column — a card that asks for a bearer and a
skill with no `credentials_ref` reads as *needs credential*, because the refusal would otherwise
come from the far side. Two reads back it:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/a2a/probe?card_url=…` | Fetch a remote Agent Card through the runtime and report its name, skills and whether it wants a bearer; `supported: false` carries the reason (a 404 usually means A2A is off over there). Reads nothing local, writes nothing. |
| `GET` | `/api/a2a/agents` | Every `agent` skill with a `card_url`: id, card, skill, credential, `on_unanswerable`, tier. |

Adding one writes the skill through `PUT /api/skills/{id}` — the same path as any skill — so the
file is the record and the portal holds no state of its own.

## See also

- [Workspace artifact](workspace.md) — the `credentials` and `mcp_servers` fields.
- [HTTP API](http-api.md) — every `/api/oauth/*` and `/auth/mcp/*` route.
- [Environment variables](cli.md#environment-variables) — `SWARMKIT_OAUTH_KEY`, `SWARMKIT_OAUTH_RUN_WINDOW_S`.
