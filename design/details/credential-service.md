---
title: One credential service, every entry point
description: Why acquisition and refresh belong at a service layer rather than at run start, and what breaks when they do not.
status: draft
---

# One credential service

## The bug that produced this note

`mcp-oauth.md`'s pre-run refresh shipped as a hook inside `WorkspaceRuntime.run()`, guarded by
`owns_mcp = not self._session_active`. It is correct for exactly one caller.

| entry point | starts servers via | session active | refresh ran |
| --- | --- | --- | --- |
| `swarmkit run` | `run()` → `start_required` | no | yes |
| `swarmkit chat` | `start_session()` | yes | **no** |
| `swarmkit serve` | `start_session()` | yes | **no** |
| `swarmkit mcp-serve` | `start_session()` | yes | **no** |

Backwards, and not by a little: `serve` is the long-lived process where a token actually expires,
and it is the one path that skipped the check. A ninety-second CLI run got a refresh it rarely
needed; a week-old serve process got none.

Two more holes sat behind it. An HTTP server's `Authorization` header is resolved once in
`_start_http` and held for the session's lifetime, so a refreshed token would update the database
and change nothing on the wire. And nothing connected the token store to the MCP client at all —
`grep -rn "TokenStore" mcp/` returned nothing, and `credential_ref.source` had no `oauth` member —
so a token could be obtained, stored and refreshed while every call still went out unauthenticated.

**The diagnosis that matters is not "three holes".** It is that acquisition and refresh were written
at a *call site* instead of at a *service*, so each entry point needed to remember them, and the
one nobody thought of was the one that mattered. Patching three call sites would have left the
fourth to be discovered the same way.

## The seam already exists on paper

`workspace-schema-v1.md` §`credentials` specifies it:

> Every credential declaration shares a **uniform shape**: `{ source, config }`. The `source`
> selects the SecretsProvider … Mirrors the `GovernanceProvider` and `ModelProvider` patterns.

It was never built. In its place are two ad-hoc resolvers — `auth/_secrets.py` for serve-auth
`key_ref`s and `mcp/_credentials.py` for MCP servers — each handling `env` and `file` and raising
`NotImplementedError` for everything else. The OAuth work then added a third path. Three resolvers,
no service.

## The decision

**Build the specified seam, make `oauth` one of its sources, and delete the run-start hook.**

```
                     ┌──────────────────────────────┐
  run · chat · serve │  CredentialService           │
  mcp-serve · CLI ──►│  resolve(ref) -> secret      │
  triggers · fleet   │                              │
                     │  env · file · vault · oauth  │
                     └──────────────────────────────┘
```

**Refresh stops being a step and becomes a property of resolution.** `resolve("linear")` on an
`oauth` credential returns a *valid* access token — refreshing first if the stored one is inside
the expiry window. No caller asks for a refresh, so no caller can forget, and an entry point added
next year inherits the behaviour by resolving a credential like everything else.

That is the same shape as the seams this repo already trusts: nothing calls "please evaluate
governance" as a separate step either — `governed_mcp_call` resolves it because that is the only
path a tool call can take.

## What this deletes

`WorkspaceRuntime._refresh_oauth_for` and both of its call sites. A fix that *adds* a fourth
special case to the three that already exist would be repeating the mistake; the measure of this
change is that run/chat/serve end up with no OAuth-specific code at all.

## Resolution happens at the point of use

For stdio servers the secret is injected into the child's environment at spawn, so connect-time
resolution is the point of use and nothing changes.

For HTTP servers it is not. A header resolved once and held for the session's life pins whatever
token was valid at startup, which under `serve` can be days stale. So HTTP credential resolution
moves to request time. This is the same class of error as caching a permission decision: correct
at the moment it was taken, wrong by the time it is used.

## Non-goals

- **Not a new secrets backend.** Vault, AWS, GCP and Azure stay `NotImplementedError` until
  someone needs them; this is about *where resolution happens*, not how many sources exist.
- **Not a credential cache with its own TTL.** The provider's expiry is the only clock. A second
  TTL would be a second source of truth about whether a token is alive.
- **Not touching governance.** What a credential authorises is unchanged — `iam.required_scopes`
  authorises, and a credential is only how the call is signed.

## Test plan

- One test per entry point asserting a stale credential is refreshed before use — the assertion
  whose absence is this note's first section. `run`, `chat`, `serve` and `mcp-serve` each start
  servers by a different path, and each must arrive at the same service.
- A resolver test per source, including `oauth` returning a *fresh* token when the stored one is
  inside the window and raising `ConsentRequired` when it cannot.
- An HTTP server whose token changes between two calls sends the new one on the second — the
  connect-time-binding bug, as a test.
- The negative: `grep` for OAuth-specific logic outside the service finds none.

## Demo plan

`just demo-oauth-refresh` — a stub provider issuing 5-second tokens against a workspace served by
`swarmkit serve`. Two calls a minute apart both succeed, and the audit log shows the refresh
between them. Under the shipped code the second call fails, which is the demo.
