# Control plane — implementation progress

Living tracker for building the control plane. Design is complete (docs 00–18); this tracks the
**code**. Update the status boxes + changelog as slices land. Phases follow doc 11 §7.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done (PR #) · `[-]` deferred/blocked.

## Phase status

| Phase | Area | Design | Implementation |
|---|---|---|---|
| 0 | Inventory | ✅ #365 | n/a |
| 1 | Architecture | ✅ #366 | n/a |
| 2 | Auth design + `server.auth` spec | ✅ #368 | ✅ (realised in Phase 3) |
| 3 | **Auth implementation** + connector/registry | ✅ #368/#369/#370 | ✅ #371–#384 |
| 4 | Aggregation | ✅ #369 | ✅ #385–#388, #396 |
| 5 | Artifact registry + versioning | ✅ #369 | ✅ #389–#391, #394–#395 |
| 6 | Fleet UI | ✅ #369 | ✅ #375–#401 (full page set) |
| 7 | Growth loop | ✅ #369 | **manual path done** (#392/#393/#401); automation pending |
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
- [x] **panel authentication** (doc [12](12-auth.md) §3) — bearer auth with two principals:
      operator tokens (full access, `--operator-token` / env) and connector tokens (a Mode B
      instance's minted token, matched by stored hash, scoped to its own poll + result routes).
      Open (no-auth) when no operator tokens are set. (PR #381)
- [x] human→panel OIDC (doc [12](12-auth.md) §3, backend) — the panel verifies OIDC JWTs
      (RS256/ES256 + JWKS, validate iss/aud/exp/sub) and authenticates the caller as an operator;
      `--oidc-issuer` / `--oidc-audience` / `--oidc-jwks-url` / env (PR #382)
- [x] UI OIDC login flow — browser PKCE auth-code (react-oidc-context), opt-in via
      `NEXT_PUBLIC_OIDC_*`; gates the app behind sign-in, sends the token as `Authorization: Bearer`
      on every panel call, re-initiates login on 401, sign-out in the sidebar (PR #383)
- [x] artifact registry — done in Phase 5 (PR #389)

## Phase 4 — Aggregation (doc [14](14-aggregation.md))

### Slice 1 — push API + central store + rollups (PR #385)
- [x] `AggregationStore` (sqlite, append-only, deduped by `(instance_id, kind, record_id)`) for the
      three pushed signals — audit / eval / usage
- [x] `POST /aggregate/{audit|eval|usage}` ingestion — connectors push as themselves (instance_id
      from the principal, no spoofing); operators/open-mode name the instance in the body
- [x] SwarmKit-specific rollups: `GET /usage` (tokens/cost by model+provider), `GET /eval`
      (pass-rate by eval_set+topology), `GET /audit` (recent fleet events)
- [x] auth: connectors may push `/aggregate/*`; reads are operator-only

### Slice 2 — observability bundle (PR #386)
- [x] turnkey OTel stack at `deploy/observability/` (docker-compose): OTel Collector → Jaeger
      (traces) + Prometheus → Grafana with a prebuilt **SwarmKit Fleet** dashboard. Resolves doc 14's
      "recommended bundle vs BYO" open question (ship a default; stay BYO-friendly). Verified e2e:
      OTLP → collector → Jaeger, Prometheus scraping the collector, dashboard provisioned.
- [x] documented fan-out (one instance → one collector → many backends) — answers "multiple
      collectors"; native multi-endpoint export from the instance is a noted future runtime option
- [x] panel observability config — `--collector-endpoint`/`--jaeger-url`/`--grafana-url` + env +
      `GET /observability`; fleet-UI **Observability card** deep-links Jaeger/Grafana + shows the
      collector endpoint (PR #387). Also fixed the bundle's collector to promote `service.name` →
      `service_name` label (`resource_to_telemetry_conversion`) so the Grafana dashboard filters
      per instance — verified end-to-end with real runtime telemetry (Jaeger swarmkit.* traces +
      dashboard data).
- [x] UI Runs/Evals pages over the rollups — `/runs` (usage by model/provider + recent audit
      activity) and `/evals` (pass-rate by eval_set/topology, color-coded); both sidebar items now
      live (PR #388)
- [x] federated live-job query — `GET /instances/{id}/jobs` pulls the instance's serve `/jobs` live
      (Mode A; poll instances → 409, not reachable); not stored. Fleet-UI **Live jobs** card on the
      instance detail page (direct instances), color-coded status (PR #396)
- cost analytics stays blocked until ModelProviders populate `cost_usd` (doc 14, carried forward)

## Phase 5 — Artifact registry (doc [15](15-artifact-registry.md))

### Slice 1 — registry store + versioning + deployments + drift (PR #389)
- [x] `ArtifactStore` (sqlite) — versioned artifacts (topology/skill/archetype/workspace/trigger)
      with `content_hash` + provenance (`authored_by`, `created_at`, `schema_version`); identical
      content is idempotent, changed content is a new version (never a silent overwrite)
- [x] API: `POST /artifacts/{kind}/{id}/versions`, `GET /artifacts`, `GET …/versions[/{version}]`
- [x] deployments (registry-intended version per instance): `PUT/GET /instances/{id}/deployments`
- [x] **drift detection**: `POST /instances/{id}/artifacts/report` (connector-scoped) + `GET
      /instances/{id}/drift` (intended vs reported → ok / drift / missing)
- [x] governed push to instances — `POST /instances/{id}/deploy` (operator-only, audited): Mode A
      PUTs to the instance's serve `/api`, Mode B enqueues a `deploy` command the connector applies;
      records the registry-intended version. Deploys only what's already published (PR #394)
- [x] schema-compatibility gate — deploy refuses an artifact the instance can't validate (same
      major + instance schema ≥ artifact schema; unknown on either side is not gated) → 409 (PR #395)
- [x] UI artifact-registry surface — `/artifacts` (list by kind/id + latest/versions/hash) and
      `/artifacts/[kind]/[id]` (version history + provenance + content viewer); Artifacts sidebar
      item now live (PR #390)
- [x] UI per-instance **Deployments & drift** card on the instance detail page — set the intended
      version + a drift table (intended vs actual → ok/drift/missing, color-coded) (PR #391)
- storage is sqlite for now; design's git-backed content store + Postgres is the later swap

## Phase 7 — Growth loop / approval gates (doc [17](17-growth-loop.md))

### Slice 1 — proposal pipeline + human approval gate (PR #392)
- [x] `ProposalStore` (sqlite) — the approval queue. A proposal (signal → drafted artifact change)
      starts `pending`; **nothing auto-approves** (no code path leaves `pending` except explicit
      approve/reject — a hard, tested invariant)
- [x] API: `POST /proposals` (open), `GET /proposals[?status]`, `GET /proposals/{id}`,
      `POST /proposals/{id}/approve`, `POST /proposals/{id}/reject`
- [x] **the human gate**: approving publishes the proposed content as a new **registry version**
      (provenance carries proposer + approving human); approve/reject are operator-only — a
      connector (machine token) is denied (separation of powers §8.7)
- [ ] cross-instance skill-gap aggregation + ranking (signal → surface)
- [ ] authoring-swarm draft + eval-harness test stages (propose → test)
- [x] governed deploy of an approved version to target instances (publish → deploy) — see Phase 5
      `POST /instances/{id}/deploy` + the connector `deploy` verb (PR #394)
- [x] Approvals UI — `/approvals` queue (proposed content + provenance + signal; pending-only/all
      filter) with approve-&-publish / reject actions; Approvals sidebar item now live (PR #393)

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
- [x] enroll-instance form (`/instances/new`) — name/endpoint/connection/tier/token-ref →
      `POST /instances` → redirect to the new instance's detail page (PR #380)
- [x] **Settings** page (`/settings`) — read-only view of panel + UI config over `GET /config`
      (version, auth flags, observability links, CORS origins; flags/URLs only, never secrets);
      Settings sidebar item now live (PR #397)
- [x] **Authoring** page (`/authoring`) — conversational front-end to an instance's authoring
      swarm: chat → drafted-artifact preview → **Propose for approval** (into the growth-loop
      queue). Panel `POST /instances/{id}/author` (operator-only, Mode A) drives the swarm via
      serve and extracts a `{kind,id,content}` draft; **last stubbed sidebar item now live** (PR #401)
- [x] OIDC login + 401 handling (PR #383/#384); the Phase 4–7 surfaces shipped as top-level pages
      — Runs + Evals (#388), Artifacts (#390), Approvals (#393), Authoring (#401)
- [ ] a **global instance selector** (each page currently resolves its own instance)

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
- **#380** — Phase 6 fleet UI: enroll-instance form (`/instances/new`) — name/endpoint/connection/
  tier/token-ref → `POST /instances` → redirect to the new instance's detail page. UI-only.
- **#381** — Phase 3 panel auth: bearer auth on the panel API — operator tokens (full access) +
  connector tokens (Mode B instance, matched by stored SHA-256, scoped to its own poll + result
  routes); open when no operator tokens configured. The minted per-instance token doubles as the
  connector→panel credential (no connector change). swarmkit-control-plane 0.5.0.
- **#382** — Phase 3 OIDC (human→panel, backend): the panel verifies OIDC JWTs (PyJWT, RS256/ES256
  + JWKS discovery, iss/aud/exp/sub) as a third auth path → operator principal carrying the subject;
  `--oidc-issuer`/`--oidc-audience`/`--oidc-jwks-url` + env. swarmkit-control-plane 0.6.0.
- **#383** — Phase 6 fleet UI OIDC login: browser PKCE auth-code (react-oidc-context + oidc-client-ts),
  opt-in via `NEXT_PUBLIC_OIDC_*`. Gates the app behind sign-in, attaches the token as
  `Authorization: Bearer` on panel calls, re-initiates login on 401, sign-out in the sidebar; open
  (no login) when unconfigured. Closes the human→panel loop with #382. UI-only.
- **#384** — OIDC login e2e in the suite: Playwright drives the real browser PKCE flow against a
  fake OIDC IdP + the OIDC-enabled panel + the UI (`e2e/`, `playwright.config.ts`), asserting the
  panel accepts the issued token. New CI `e2e` job. UI-only.
- **#385** — Phase 4 aggregation slice 1: `AggregationStore` (append-only, deduped) + push API
  `POST /aggregate/{audit|eval|usage}` (connector-scoped) + rollups `GET /usage` (by model/provider),
  `GET /eval` (pass-rate), `GET /audit` (recent fleet). swarmkit-control-plane 0.7.0.
- **#386** — Phase 4 observability bundle: `deploy/observability/` turnkey docker-compose (OTel
  Collector → Jaeger + Prometheus → Grafana w/ a prebuilt SwarmKit dashboard); fan-out documented as
  the multi-backend pattern. Infra/docs only (no package change).
- **#387** — Phase 4 observability links: panel `--collector-endpoint`/`--jaeger-url`/`--grafana-url` + `GET /observability`; fleet-UI Observability card deep-links the dashboards. Also fixes the bundle collector to label metrics with `service_name` so the Grafana dashboard filters per instance. swarmkit-control-plane 0.8.0.
- **#388** — Phase 6 fleet UI: `/runs` (fleet usage by model/provider + recent audit activity) and `/evals` (pass-rate by eval_set/topology, color-coded) over the aggregation rollups; Runs + Evals sidebar items activated. UI-only.
- **#389** — Phase 5 artifact registry slice 1: `ArtifactStore` (versioned artifacts + content-hash + provenance, idempotent re-register) + API (`/artifacts/*`), per-instance deployments, and drift detection (`/instances/{id}/artifacts/report` + `/drift`). swarmkit-control-plane 0.9.0.
- **#390** — Phase 5 artifact-registry UI: `/artifacts` (registry list) + `/artifacts/[kind]/[id]` (version history, provenance, content viewer); Artifacts sidebar item activated. UI-only.
- **#391** — Phase 5 UI: per-instance Deployments & drift card on the instance detail page — set the intended version + a color-coded drift table (intended vs reported actual). UI-only.
- **#392** — Phase 7 approval gate slice 1: `ProposalStore` + proposal pipeline (`/proposals` open/list/get/approve/reject). Approval is the human gate — it publishes the proposed content as a new registry version; nothing auto-approves and connectors (machines) are denied. swarmkit-control-plane 0.10.0.
- **#393** — Phase 7 Approvals UI: `/approvals` proposal queue (content + provenance + signal, pending/all filter) with approve-&-publish / reject; Approvals sidebar item activated. UI-only.
- **#394** — Phase 5/7 governed deploy: `POST /instances/{id}/deploy` (operator-only, audited) pushes a published registry version to an instance — Mode A PUTs to serve `/api`, Mode B enqueues a `deploy` command + new connector `deploy` verb. Closes publish→deploy. swarmkit-control-plane 0.11.0 / runtime 1.13.0.
- **#395** — Phase 5 schema-compat gate: governed deploy refuses an artifact an instance can't validate (`_compat.schema_compatible`: same major + instance schema ≥ artifact schema; unknown → allowed) → 409. swarmkit-control-plane 0.12.0.
- **#396** — Phase 4 federated live-jobs: `GET /instances/{id}/jobs` pulls the instance's serve `/jobs` on demand (Mode A; poll → 409), not stored; fleet-UI **Live jobs** card on the instance detail page. swarmkit-control-plane 0.13.0.
- **#397** — Phase 6 fleet UI Settings: panel `GET /config` (read-only — version + auth flags + observability links + CORS origins; flags/URLs only, never token values, operator-only under auth) + a `/settings` page (Panel / Authentication / Observability / CORS cards, open-mode warning); Settings sidebar item activated. swarmkit-control-plane 0.14.0.
- **#401** — Phase 6 fleet UI Authoring: conversational front-end to an instance's authoring swarm — panel `POST /instances/{id}/author` (operator-only, Mode A: runs the authoring topology on serve + extracts a `{kind,id,content}` draft) and an `/authoring` page (chat → drafted-artifact preview → **Propose for approval** into the growth-loop queue). Last stubbed sidebar item now live; the whole doc-16 page set ships. Demo: `packages/control-plane/demos/authoring_swarm.py`. swarmkit-control-plane 0.15.0.
