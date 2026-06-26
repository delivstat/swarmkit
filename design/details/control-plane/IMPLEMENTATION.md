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
- [x] `GET /capabilities` on serve (PR #373)
- [x] **control-plane app scaffold** — new monorepo package `packages/control-plane`
      (`swarmkit-control-plane`): FastAPI panel API + sqlite instance registry, wired into the
      uv workspace + CI (PR #374)
- [x] panel instance registry (CRUD + health + `connection` mode) + enrollment (Mode A pull-verify
      via the instance's `/capabilities`) (PR #374)
- [x] heartbeat receiver — `POST /instances/{id}/heartbeat` (Mode A + Mode B liveness) (PR #374)
- [ ] Mode B poll command-queue (`/instances/{id}/poll` + `/commands/{id}/result`) + the
      `swarmkit connect` instance-side connector
- [ ] token minting in the panel (enrollment currently takes an operator-supplied `token_ref`)
- [ ] aggregation, artifact registry, fleet UI — Phases 4–6

> Repo placement decided: **new monorepo package** (`packages/control-plane`; the fleet UI will be
> a sibling package). Next: the Mode B poll command-queue + `swarmkit connect`.

## Changelog
- **#371** — Phase 3 auth slice 1: `server.auth` schema + `serve:*` tiers + per-route authorize
  enforcement + default-secure bind + reserved-scope guard. runtime 1.9.0 / schema 1.6.0.
- **#372** — Phase 3 auth slice 2: serve access-audit (`client_id` on mutating calls), `key_ref`
  resolution (`env:`/`file:`/`credentials:NAME`), `swarmkit auth token` mint helper, operator
  docs. runtime 1.10.0.
- **#373** — Phase 3 connector: `GET /capabilities` (instance capability advertisement, the
  enrollment-verify surface). runtime 1.11.0.
- **#374** — control-plane app scaffold: new `packages/control-plane` package (panel API + sqlite
  instance registry + enrollment with Mode A pull-verify + heartbeat receiver), wired into the uv
  workspace + CI. swarmkit-control-plane 0.1.0.
