---
title: serve --profile production — a fail-closed startup preflight
description: An opt-in profile that refuses to start serve unless the deployment is configured fail-closed (real auth, a persistent secret key, sandboxed MCP, no wildcard CORS, not --insecure). Addresses the scaling/security review's "production defaults too permissive".
tags: [serve, security, operations, governance]
status: proposed
---

# serve --profile production

## The gap

An external review flagged that serve's defaults are permissive for convenience — anonymous auth on
loopback, an ephemeral OAuth key generated on first run, MCP servers that run unsandboxed unless the
author sets `sandboxed: true`. Each is the right *default* for `swarmkit serve` on a laptop, and the
wrong one in production. Today an operator has to know and check every one; nothing asserts "this
deployment is safe to expose". Default-secure already refuses an unauthenticated non-loopback bind —
but that is one check, not a profile.

## Goal

`swarmkit serve --profile production` runs a **preflight** at startup and **refuses to start** —
listing every violation at once — unless the deployment is fail-closed:

1. **Real auth.** The auth provider is not `none`/anonymous (an `api_key` or `jwt` provider is
   configured). Production does not serve anonymous, on any bind.
2. **Not `--insecure`.** The escape hatch is incompatible with the profile; passing both is refused.
3. **A persistent secret key.** `SWARMKIT_OAUTH_KEY` is set, so the OAuth token-encryption key does
   not regenerate on restart (which would silently invalidate every stored token — the fleet-panel
   `InvalidToken`-on-restart failure, generalised).
4. **Sandboxed MCP.** Every declared `mcp_servers` entry has `sandboxed: true` (design §8.8) — a
   production server does not run a tool process unisolated on the host.
5. **No wildcard CORS.** `--cors-origin *` is refused (exact origins only).

The profile does not *silently change* behaviour — it asserts the operator configured the safe
thing, and fails loudly with the exact list when they did not. `standard` (the default) is today's
behaviour, unchanged.

## Non-goals

- **Not new runtime enforcement.** Governance is already deny-by-default at the policy engine; this
  does not add a second policy layer. It is a deployment-time assertion, not a request-time gate.
- **Not TLS termination.** Terminating TLS is the reverse proxy's job; the profile does not check for
  it (there is nothing in-process to check).
- **Not a replacement for default-secure.** The existing non-loopback+anonymous refusal stays and
  runs regardless of profile; production adds to it.

## API shape

```bash
swarmkit serve --profile production            # refuses unless fail-closed; lists every gap
swarmkit serve                                 # --profile standard (default), unchanged
```

`create_app(..., profile="production")` runs the preflight so every embedder inherits it, not only
the CLI. A violation raises `RuntimeError` with all gaps; the CLI maps it to a friendly message +
exit code, exactly like the existing default-secure refusal.

## Test plan

- A workspace that is fully fail-closed (jwt auth, `SWARMKIT_OAUTH_KEY` set, every MCP server
  `sandboxed: true`) starts under `--profile production`.
- Each violation, in isolation, is refused with a message naming it: anonymous auth; `--insecure`;
  missing `SWARMKIT_OAUTH_KEY`; an unsandboxed MCP server; a `*` CORS origin.
- Multiple violations are reported together, not one at a time.
- `--profile standard` (default) is unaffected — a permissive laptop workspace still starts.

## Demo plan

CLI transcript: `swarmkit serve --profile production` on a permissive workspace prints the numbered
gap list and exits non-zero; after fixing them, it starts.
