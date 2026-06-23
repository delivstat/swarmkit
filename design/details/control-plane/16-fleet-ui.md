# 16 — Phase 6: Fleet UI

Builds on [09](09-ui.md) (the existing single-instance UI + its reusable `lib/api.ts`) and D1/D4
([11](11-architecture.md)). Designs the **separate standalone application** that is the control
plane's human surface.

## Goal

A multi-instance panel: see the fleet, manage instances + artifacts, observe runs/evals/audit,
approve changes, and author conversationally — talking to the control-plane API + each instance's
serve.

## Pages

- **Fleet overview** — instances + health ([13](13-connector-registry.md)), recent activity,
  alerts (drift, eval regressions, unreachable instances).
- **Instances** — enroll/manage, capabilities, tokens, schema version, labels.
- **Runs** — cross-instance run list (federated live + recent), with **links out to the OTel
  collector** for full traces ([14](14-aggregation.md)); trigger/cancel (cancel needs `stop`, M6).
- **Evals** — pass-rate trends + regressions across instances ([06](06-observability-eval.md),
  [14](14-aggregation.md)).
- **Artifact registry** — browse/diff versions, provenance, promote/rollback, **deploy (governed,
  human-gated)** ([15](15-artifact-registry.md)); drift indicators.
- **Approvals** — fleet HITL queue ([05](05-identity-governance-iam.md), [17](17-growth-loop.md)),
  identity-gated.
- **Authoring** — **conversational** front-end to the authoring swarm (the composer's missing half,
  [09](09-ui.md)): chat → structured artifact preview → test (eval) → propose for approval. Not
  another YAML textarea.
- **Settings** — OIDC, tokens, collector endpoint, RBAC.

## Reuse vs new

- **Reuse:** the `lib/api.ts` REST/SSE patterns + `@swarmkit/schema` for validation/types.
- **New for multi-instance:** an **instance selector** + per-instance base URL; **auth-header
  injection**; **OIDC login** + session ([12](12-auth.md)); global **401→login** handling; optional
  **RBAC-aware** gating. The existing single-instance app stays as the OSS single-box dashboard
  ([09](09-ui.md)); the fleet panel is the separate app (D1).

## Build vs embed

- **Embed/link** the OTel ecosystem for raw traces/metrics ([14](14-aggregation.md)).
- **Build** the SwarmKit-specific views (registry, evals, drift, governance, approvals, authoring) —
  that's the panel's reason to exist.

## Tech + placement

A standalone app (Next.js is the natural default given the existing stack + `lib/api.ts` reuse,
but not mandated). Depends on `@swarmkit/schema` + the serve connector contract — **not** on the
runtime internals. **Repo placement** (own repo vs new `packages/` member) is the one open
decision to confirm before this phase starts ([11](11-architecture.md) §8).

## What Phase 6 builds

The standalone app shell + auth (OIDC + per-instance tokens + instance selector); the pages above;
the conversational-authoring surface (fronting the authoring swarm via serve); registry browse/diff/
deploy UI; approvals UI. Depends on Phases 2–5.

## Open questions

- Repo placement (own repo vs monorepo package) — confirm.
- How much of the existing single-instance dashboard's components are lifted vs rebuilt.
- RBAC model granularity (roles → which pages/actions) — likely maps to OIDC groups + `serve:*` tiers.
