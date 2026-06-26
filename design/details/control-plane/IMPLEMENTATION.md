# Control plane — implementation progress

Living tracker for building the control plane. Design is complete (docs 00–18); this tracks the
**code**. Update the status boxes + changelog as slices land. Phases follow doc 11 §7.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done (PR #) · `[-]` deferred/blocked.

## Phase status

| Phase | Area | Design | Implementation |
|---|---|---|---|
| 0 | Inventory | ✅ #365 | n/a |
| 1 | Architecture | ✅ #366 | n/a |
| 2 | Auth design + `server.auth` spec | ✅ #368 | **in progress** (Phase 3 below) |
| 3 | **Auth implementation** + connector/registry | ✅ #368/#369/#370 | **active** |
| 4 | Aggregation | ✅ #369 | not started |
| 5 | Artifact registry + versioning | ✅ #369 | not started |
| 6 | Fleet UI | ✅ #369 | not started |
| 7 | Growth loop | ✅ #369 | not started |
| 8 | Hardening + rollout | ✅ #369/#370 | not started |

## Phase 3 — Auth implementation (doc [12](12-auth.md) §9)

Hardening the existing `auth/` seam. Slices:

### Slice 1 — schema + tiers + per-route enforcement + default-secure ✅ (PR #371, runtime 1.9.0)
- [x] `server.auth` block in `workspace.schema.json` (+ bundled copy, regen pydantic & TS, fixtures)
- [x] transport scope tiers `serve:read|run|admin` (`auth/_scopes.py`) + tier→scopes expansion in `APIKeyAuthProvider`
- [x] per-route required-scope map (`server._required_action`) + wire `authorize()` in `auth_middleware` (→ 403)
- [x] default-secure: non-loopback bind + `provider: none` refuses to start (`--insecure` /
      `require_on_nonloopback: false` escape)
- [x] reserved-scope guard (no key may grant `skills:activate`/`mcp_servers:deploy`/
      `topologies:modify`/`iam:modify`/`audit:*`)
- [x] tests (tier expansion, route enforcement, default-secure, schema fixtures) + runtime → 1.9.0

### Slice 2 — audit, secret resolution, mint, docs ✅ (PR #372, runtime 1.10.0)
- [x] audit the acting `client_id` on authenticated mutating calls (`serve_access` table + middleware)
- [x] `key_ref` secret resolution: `env:` / `file:` / `credentials:NAME` (env+file sources; cloud
      sources raise until a SecretsProvider is wired)
- [x] token mint helper — `swarmkit auth token <client-id> --tier`
- [x] operator docs — `docs/guides/serve-auth.md` (api_key + jwt/OIDC, tiers, key_ref, rotate/revoke,
      default-secure)

## Phase 3 — Connector + registry (doc [13](13-connector-registry.md))
- [x] `GET /capabilities` on serve (PR #373) — serve_version, schema_version, topologies,
      model_providers, governance_provider, features (auth/compression/canary)
- [-] Mode A heartbeat (instance→panel) + panel `POST /instances/{id}/heartbeat` — **needs the
      panel app** (the receiver). Instance-side heartbeat client lands with the panel.
- [-] Mode B poll connector (`swarmkit connect`) + panel command-queue endpoints — **needs the
      panel app** (the command queue to poll).
- [-] panel instance registry (CRUD + health + `connection` mode) + enrollment + token minting —
      **the panel app itself** (separate standalone application, D1).

> The remaining connector pieces all require the standalone control-plane **panel** (the other end
> of the connection). `/capabilities` + the serve-side auth/audit are the instance-side surface the
> panel will consume. Next real step is **scaffolding the panel app** (decide repo placement first).

## Changelog
- **#371** — Phase 3 auth slice 1: `server.auth` schema + `serve:*` tiers + per-route authorize
  enforcement + default-secure bind + reserved-scope guard. runtime 1.9.0 / schema 1.6.0.
- **#372** — Phase 3 auth slice 2: serve access-audit (`client_id` on mutating calls), `key_ref`
  resolution (`env:`/`file:`/`credentials:NAME`), `swarmkit auth token` mint helper, operator
  docs. runtime 1.10.0.
- **#373** — Phase 3 connector: `GET /capabilities` (instance capability advertisement, the
  enrollment-verify surface). runtime 1.11.0.
