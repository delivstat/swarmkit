# Persistent OAuth clients + Desktop-app flow

**Status:** draft, part 1 of [[hermes-oauth-lessons]] (issue #1000).
**Depends on:** [[mcp-oauth]] (current OAuth surface).
**Blocks:** [[dot-app]] real-runtime wiring; [[google-workspace-setup]] (part 2 of #1000).

## Goal

Make it possible for a self-hosted SwarmKit install to serve many users against providers that
do **not** support dynamic client registration (DCR) — Google, GitHub, Notion, every major
provider — without the operator having to paste a `client_id` into every single OAuth login
request.

Also document and support Google's "Desktop app" client type as the recommended path for
self-hosted installs: it accepts any `http://localhost:<port>` or `http://127.0.0.1:<port>`
callback without pre-registering the exact URL, so one `client_id`/`client_secret` pair works
across every machine, port, and install.

## Non-goals

- Replacing the existing DCR path. Providers that support DCR (we discovered one in the
  wild — the official MCP registry sample servers) keep working with no config.
- Multi-tenant key management. A single install registers at most one OAuth client per
  provider endpoint; distinguishing which user owns which client is out of scope here (that's
  the hosted-SaaS story, deferred).
- Setup UI (the "upload your credentials.json and we parse it" surface). That is
  [[google-workspace-setup]], part 2 of #1000.
- The `urn:ietf:wg:oauth:2.0:oob` manual-paste flow. Google deprecated it for new clients in
  Feb 2022 and the localhost loopback path replaces it cleanly for Desktop clients.

## Problem — in detail

Today the runtime distinguishes two OAuth paths at `prepare_login` time
(`packages/runtime/src/swarmkit_runtime/server/_routes_oauth.py:110`):

- If `discover_metadata` + `register_client` succeed, we have a `client_id` and continue.
- Else we require `client_id` in the login request body or raise 400.

The second branch is the Google case. The user has to:

1. Create a GCP project, enable the APIs, configure the consent screen, generate an OAuth
   client, download the JSON. Fine — one-time, [[hermes-oauth-lessons]] #2 covers walking
   this with an agent.
2. **Pass the `client_id` on every single `POST /api/oauth/login`.** The portal has to carry
   it. The DOT connections page has to carry it. A CLI mint has to carry it. Every surface
   that initiates an OAuth login carries the client_id, or every surface asks the user to
   paste it again. This is the real sharp edge.

On top of that, every attempt that goes to the token-exchange step (`exchange_code`,
`_pkce.py:215`) also needs the `client_secret` for a Desktop app client. We don't accept one
today — `exchange_code` builds the POST body with `client_id` and `code_verifier` but no
`client_secret`. That works for public clients, fails for Google's Desktop clients which
require it even with PKCE.

## Proposal

### A persistent "oauth_clients" store

A new table / backend-abstraction row keyed by `(endpoint, scope_set)`:

```
oauth_clients
  endpoint         text        -- e.g. "https://gmail.googleapis.com/..."
  client_id        text
  client_secret    text        -- encrypted at rest, same box as oauth_tokens
  client_type      text        -- "desktop" | "web" | "dcr"
  created_at       timestamp
  display_name     text
  notes            text        -- optional operator note
```

- One row per endpoint, operator-maintained.
- Encrypted with the same storage secret as `oauth_tokens`.
- Discoverable via `GET /api/oauth/clients` (admin scope, lists endpoints + display names;
  does **not** return secrets).
- Written via `POST /api/oauth/clients {endpoint, client_id, client_secret?, client_type,
  display_name, scopes?}` (admin scope).
- Removed via `DELETE /api/oauth/clients/{endpoint}` (admin scope).

### `prepare_login` auto-resolves

When a login arrives:

1. Try DCR (unchanged).
2. If DCR fails or isn't supported, look up a persistent client for the endpoint.
3. If found, use its `client_id` and (if `client_type == "desktop"`) carry its `client_secret`
   through to the eventual `exchange_code` call.
4. If not found, 400 with a helpful message naming the endpoint and pointing to the setup
   docs — same message every surface shows, so a user who hits it twice recognises it.

### `exchange_code` accepts an optional `client_secret`

```python
async def exchange_code(
    metadata, *, code, verifier, client_id,
    client_secret: str | None = None,     # NEW
    redirect_uri, client,
) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    ...
```

Carried through `PendingLogin.metadata["client_secret"]` only in memory; the DB row is the
authoritative home. The pending-login window is ~5 min, same as today.

### Documentation + recommendation

A new short page `docs/notes/oauth-setup.md` says: for self-hosted installs, prefer Google's
**Desktop app** client type. It accepts any `http://localhost` or `http://127.0.0.1` redirect
without pre-registration, so one credential pair follows the install across machines, ports
and WSL redirects. Web-app type stays supported for hosted deployments that have a stable
public callback URL.

## API shape

```
POST /api/oauth/clients
    { "endpoint": "...", "client_id": "...", "client_secret": "...",
      "client_type": "desktop", "display_name": "Google (DOT appliance)",
      "scopes": [...] }
    → 201 { "endpoint": "...", "display_name": "..." }

GET  /api/oauth/clients
    → { "clients": [{ "endpoint": "...", "display_name": "...",
                      "client_type": "desktop", "created_at": "..." }] }

DELETE /api/oauth/clients/{endpoint-id}
    → 204
```

`endpoint-id` is a URL-safe hash of the endpoint; the server also accepts the raw endpoint
URL-encoded, for operator convenience.

## Test plan

Unit:
- `oauth_clients` CRUD under both SQLite and Postgres backends (per `project_postgres_backend`).
- `prepare_login` auto-resolve: finds a stored client when DCR fails; still errors cleanly
  when neither path works.
- `exchange_code` sends `client_secret` iff provided; omits it for public clients.
- Round-trip: register client → login → callback → exchange → stored token is usable.

Integration (`tests/integration/`):
- Full Google-style flow against a mock OAuth server that mimics the GCP shape (rejects
  token exchange without `client_secret`, accepts localhost redirect without
  pre-registration).

Boundary:
- `/api/oauth/clients` requires admin scope. Non-admin → 403, mirroring the existing
  pattern in `_routes_oauth.py` for the admin-only credential-list surface.

## Demo plan

A new `just demo-oauth-desktop` target boots a mock GCP-style OAuth server, registers a
client via `POST /api/oauth/clients`, drives a `/api/oauth/login`, completes the callback,
and prints the resulting (encrypted) token row — no interactive browser step needed. The
output is a short transcript suitable for pasting into the PR body.

## Rollout

- Schema migration: adds `oauth_clients` table in both SQLite and Postgres. Pure addition;
  no existing columns move.
- Config: no new env vars. The storage secret is reused.
- Backwards-compat: existing login flows that pass `client_id` in the body keep working. If
  both a body `client_id` and a stored client exist for the same endpoint, body wins —
  explicit over implicit. (Logged at INFO so operators can spot drift.)

## Open questions

- **Q1 — Scope association.** Do we store scopes with the client, or do callers still pass
  scopes per-login? Hermes stores them with the setup skill. *Proposed default:* per-login
  (unchanged from today), with the client row carrying a `scopes` hint for display only.
- **Q2 — Encrypt client_secret or symlink to a filesystem path?** Hermes writes
  `~/.hermes/google_token.json`. We encrypt in the storage backend (anti-pattern lesson in
  #1000). *Proposed default:* encrypted in storage, no file-path alternative.
- **Q3 — Multi-tenant extension.** A hosted install may want per-owner OAuth clients (so
  usage counts and quotas attribute correctly). *Proposed default:* out of scope here;
  single-client-per-endpoint today. Revisit when a hosted-SaaS deployment surfaces.
