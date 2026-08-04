# Serving the portal's OIDC client configuration

Status: implemented (runtime 1.141.0, @swarmkit/ui 0.29.0, swarmkit-webui 0.7.0). Amends
`control-plane/12-auth.md` and `serve-hosted-webui.md`.

## The failure

With `server.auth.provider: jwt`, the portal reads `/auth-info`, gets the issuer, builds an
`oidc-client-ts` `UserManager` — and the login flow fails, because `client_id` is always `""`.

`NEXT_PUBLIC_*` variables are inlined by Next.js **at build time**. In the published artifact they
are not inlined: the bundle contains the literal source text `NEXT_PUBLIC_OIDC_CLIENT_ID)?t:""` — a
live property read against the browser `process` polyfill, whose `env` is `{}`. So the expression
evaluates to `""` on every load.

There is no runtime escape hatch. The static export contains no `env.js`, no `window.__ENV`, and no
other injection point.

**Consequently the published wheel could not be pointed at any identity provider.** An operator who
configured `provider: jwt` correctly — valid issuer, reachable JWKS, correct audience — still could
not sign in, and nothing in the UI or the server log explained why.

## Why the original design was reasonable and still wrong

The code said:

> the client_id is this UI's own registration (NEXT_PUBLIC_OIDC_CLIENT_ID) — the serve validates
> tokens, it doesn't own the browser client id

That is correct as a statement about *ownership*. It is wrong as a statement about *distribution*:
the portal is published as a pre-built wheel, so anything fixed at build time is fixed for every
deployment that installs it. A per-deployment value cannot live in a build-time constant.

## Design

**Serve `client_id` and `scope` from `/auth-info`** (report option 1). That endpoint already exists
so "a client renders the right login gate before it holds a token", and already carries `issuer` and
`audience`; the client id is part of the same answer.

```yaml
server:
  auth:
    provider: jwt
    config:
      issuer: https://id.example.com/application/o/swarmkit/
      audience: swarmkit
      client_id: swarmkit-portal
      scope: openid profile email
```

The server **never validates against these values** — it advertises them. Token validation is
untouched.

Two properties worth keeping:

- **Absent, not empty.** An unconfigured `client_id` is omitted from the response rather than sent
  as `""`, so the client's own build-time fallback still applies for a self-built UI. An empty
  string would override that fallback with nothing.
- **Validation details stay server-side.** `/auth-info` is unauthenticated, so it advertises only
  what a browser needs to *start* a login — never `jwks_url` or `scopes_claim`. A test pins the
  exact key set.

### Client

`oidcSettings` prefers the discovered value and keeps the env var as a fallback, so a self-built UI
is unaffected:

```ts
client_id: discovered.client_id || (process.env.NEXT_PUBLIC_OIDC_CLIENT_ID ?? "")
```

**Verified in the built bundle**, because the build is where this bug lived. After
`pnpm --filter @swarmkit/ui build`:

```
authority:e.issuer,client_id:e.client_id||(null!=(t=p.env.NEXT_PUBLIC_OIDC_CLIENT_ID)?t:""),…
```

The server-advertised value wins; the (broken) polyfill read survives only as the fallback.

### Fail loudly

Previously the portal called `signinRedirect()` with `client_id: ""` and the user was bounced to an
IdP error page that says nothing about which side is misconfigured. It now refuses to start the
redirect and names the setting:

> The server did not advertise an OIDC `client_id`. Set `server.auth.config.client_id` in
> workspace.yaml…

### A token path in `jwt` mode

`api_key` mode renders a paste box; `jwt` mode rendered only a sign-in button. That left no way to
use a token the operator already holds — minted by a CLI, issued by CI, obtained during IdP setup —
even though the transport is identical and the server would accept it. It also meant any breakage in
the redirect flow locked the portal completely, which is exactly what this bug did.

`jwt` mode now shows a collapsed "use a token instead" affordance writing the same storage key.
Nothing else changes: the bearer path already worked.

## Not in this change

**A dedicated `/auth/callback` redirect URI.** `redirect_uri` is `origin + pathname`, so it differs
per page and every IdP must be configured with a wildcard or regex redirect. Exact-match redirect
URIs are the OAuth 2.0 Security BCP recommendation. This is a real improvement and cheaper to make
before people have IdP configs to migrate — but it is a routing change (a callback route plus
restoring the pre-login path from `state`), it is not required for sign-in to work, and mixing it
into the fix would make the fix harder to review. Tracked separately.

**A `sub`-mismatch warning.** serve takes identity from `sub` and matches it against `members:` in
`swarm/roles.yaml`; most IdPs put a UUID there by default, which authenticates fine and then fails
every role check with a 403 indistinguishable from being unauthenticated. The startup check added
in 1.140.0 covers only providers with a *static* identity, so `jwt` needs a check at resolve time.
Also tracked separately.

## Test plan

Server — `packages/runtime/tests/test_auth_info_oidc_client.py`: advertised when configured; absent
when not; issuer/audience still travel; validation details stay server-side (exact key set); registry
factory passes them through and works without them; schema accepts them.

Client — `packages/ui/lib/oidc-config.test.ts` and `auth-info.test.ts`: server value preferred for
both keys; default scope when none advertised; `client_id` left empty rather than invented;
`missingClientId` true for empty and whitespace, false once advertised; discovery passes both
through and tolerates an older serve that advertises neither.

Schema fixture: `workspace/with-oidc-client.yaml`.

## Documentation note

Worth stating wherever the IdP guide lands: `redirect_uri` is currently per-page, so an IdP needs a
pattern redirect covering the whole origin rather than a single exact URL — until the callback route
above is built.
