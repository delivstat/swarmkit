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
| 6 | Fleet UI | ✅ #369 | **active** (slice 1 below) |
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
- [x] Mode B poll command-queue (panel: `POST /instances/{id}/commands`, `POST /poll`,
      `POST /commands/{id}/result`, `GET /commands`) + the `swarmkit connect` instance-side
      connector + granted-tier bounds on enqueue (panel) and re-validation (connector) (PR #377)
- [x] token minting in the panel — `POST /instances/{id}/mint-token` (per-instance, per-tier;
      secret shown once, only a `key_ref` + fingerprint + metadata stored) + `POST /verify`
      (Mode A re-pull) + enroll-then-mint (direct enroll without a token is unverified) (PR #378)
- [ ] aggregation, artifact registry — Phases 4–5

> Repo placement decided: **new monorepo package** (`packages/control-plane`; the fleet UI is the
> sibling package `packages/control-plane-ui`). The connector + registry + enrollment + token
> minting are complete; next is Phase 4 (observability aggregation).

## Phase 6 — Fleet UI (doc [16](16-fleet-ui.md))

### Slice 1 — app shell + fleet/instances views ✅ (PR #375)
- [x] new sibling package `packages/control-plane-ui` — Next.js 15 + React 19 + Tailwind v4 +
      shadcn/ui (zinc theme, class-based dark mode), wired into the pnpm workspace + JS CI
      (biome / tsc)
- [x] dashboard + sidebar shell; sidebar nav follows the doc-16 page set (Fleet, Instances live;
      Runs/Evals/Artifacts/Approvals/Authoring/Settings shown muted until their slices land)
- [x] `/dashboard` (Fleet) — stat cards (total / healthy / direct / poll) + instance list
- [x] `/instances` — registry table (health + connection-mode badges, schema, last-seen)
- [x] typed API client (`lib/api.ts`) over the panel API, `Instance` type mirrors
      `public_dict()`, `usePoll` refresh

### Slice 2 — instance detail + actions (PR #379)
- [x] `/instances/[id]` detail page — overview grid, capabilities, mint-token panel (secret
      shown once + `server.auth` snippet + copy), verify (Mode A) + delete actions
- [x] command-queue view for poll (Mode B) instances — live status table + enqueue form
      (tier-bounded verb select)
- [x] config-driven hosts: no hardcoded localhost — panel CORS is config-only
      (`--cors-origin` / env), UI base URL defaults to same-origin
- [ ] OIDC login + instance selector + 401 handling (later slice)
- [ ] per-instance views — runs, evals, artifact registry, approvals, conversational authoring
      (Phases 4–7 surfaces)

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
- **#375** — Phase 6 fleet UI slice 1: new `packages/control-plane-ui` (Next.js + shadcn dashboard;
  Fleet + Instances views over the panel API).
- **#377** — Phase 3 Mode B: panel command-queue (`/commands`, `/poll`, `/commands/{id}/result`)
  with granted-tier enqueue bounds + idempotent results, and the `swarmkit connect` poll connector
  (outbound-only, executes verbs against loopback serve with tier re-validation).
  swarmkit-control-plane 0.2.0 / runtime 1.12.0.
- **#378** — Phase 3 token minting: `POST /instances/{id}/mint-token` (per-instance, per-tier serve
  token; secret returned once, panel stores only `key_ref` + fingerprint + metadata) + a
  `server.auth` snippet, `POST /instances/{id}/verify` (Mode A re-pull), and enroll-then-mint
  (direct enroll without a token enrolls unverified). Also: panel **CORS** for the fleet UI via
  `--cors-origin` / `$SWARMKIT_CONTROL_PLANE_CORS_ORIGINS`. swarmkit-control-plane 0.3.0.
- **#379** — Phase 6 fleet UI slice 2: `/instances/[id]` detail page (overview, capabilities,
  mint-token panel with once-shown secret + snippet, verify + delete, and a live command-queue
  view for poll instances). Also **removes hardcoded localhost**: panel CORS is config-only (no
  localhost default) and the UI base URL defaults to same-origin. swarmkit-control-plane 0.4.0.
