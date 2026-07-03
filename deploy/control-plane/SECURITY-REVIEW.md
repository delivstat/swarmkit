# Control-plane security review (GA gate)

The pre-GA security review from design [18](../../design/details/control-plane/18-hardening-rollout.md).
Each control maps to its implementation + evidence, with a status:

- **✅ met** — implemented and tested in the codebase.
- **🔧 operator** — the mechanism exists; the operator must configure it per deployment.
- **⛔ gap** — not yet addressed (tracked).

This is a living checklist; update it as controls change. It does **not** replace a
deployment-specific review (your TLS, network, IdP, and secret-management posture).

## Auth surface (design [12](../../design/details/control-plane/12-auth.md))

| Control | Status | Evidence |
| --- | --- | --- |
| Per-route scope enforcement actually wired | ✅ met | `_auth.authorize` + the panel auth middleware gate every route; operator vs connector principals. Connector tokens are default-deny (only their own poll/result/aggregate/report routes). Tests: `test_auth.py`, and operator-only assertions in `test_authoring.py` / `test_growth.py` (connector → 403). PRs #371/#381. |
| Serve default-secure on non-loopback | ✅ met | Serve refuses a non-loopback bind with `provider: none` (PR #371); `--insecure` escape hatch documented (README "Breaking change"). |
| No token grants a reserved-for-human governance scope | ✅ met | The reserved-scope guard (`skills:activate`, `topologies:modify`, …) is structurally un-grantable (PR #371); on the panel, approve/deploy/author/propose are operator-only, machines denied. |
| Tokens are references, never literals | ✅ met | Minting stores `key_ref` + SHA-256 hash + fingerprint, never the secret; `resolve_token` handles `env:` / `file:` / literal. `token_hash` is excluded from `public_dict`. PR #378/#381. |
| Rotation / revocation tested | 🔧 operator + ✅ met | Re-mint invalidates the old token (hash changes); revoke = delete/re-enroll or rotate `server.auth`. Runbook documents it; operator performs it. |
| Panel auth actually enabled | 🔧 operator | The panel runs **open** unless `--operator-token` / `--oidc-*` is set. Compose ships it commented with a warning; the operator MUST enable it off-loopback. |

## Separation of powers (design [05](../../design/details/control-plane/05-identity-governance-iam.md), [17](../../design/details/control-plane/17-growth-loop.md))

| Control | Status | Evidence |
| --- | --- | --- |
| Artifact push (deploy) is human-gated + audited | ✅ met | `POST /instances/{id}/deploy` is operator-only + audited; deploys a *published registry version* only. PR #394. |
| Activation (approval) is human-only | ✅ met | Approving a proposal == publishing; approve/reject are operator-only, connectors denied. **No code path leaves `pending` except explicit approve/reject** (tested invariant). PRs #392/#393. |
| Growth automation cannot self-activate | ✅ met | `POST /gaps/propose` (and the authoring swarm) only ever create a **pending** proposal; a human still approves. Test: `test_growth.py` asserts `status == "pending"`. PR #404. |
| Panel cannot mutate the audit log | ✅ met | The aggregation store is append-only + deduped; no update/delete path is exposed. PR #385. |

## Audit completeness (design [14](../../design/details/control-plane/14-aggregation.md))

| Control | Status | Evidence |
| --- | --- | --- |
| Panel-triggered runs + mutations carry the acting principal + instance | ✅ met | Serve access-audit tags mutating calls with `client_id` (PR #372); the panel scopes writes to `request.state.principal`; aggregation records tag `instance_id`. |
| Central audit is append-only from the executive perspective | ✅ met | `AggregationStore` is insert-or-ignore; rollups are read-only (`GET /audit`). PR #385. |

## Transport + secrets

| Control | Status | Evidence |
| --- | --- | --- |
| TLS on human↔panel and panel↔instance | 🔧 operator | Not terminated by the app — put both behind a TLS reverse proxy. README checklist. |
| CORS locked down (no `*` in production) | ✅ met (config) | CORS is config-only — no origin is allowed unless listed via `--cors-origin`; no localhost default. PR #379. The operator sets the exact UI origin. |
| Secrets via provider / env only | ✅ met | Panel tokens are CLI/env args or refs; the UI's OIDC + API config are `NEXT_PUBLIC_*` build/runtime env. No secrets in the image or repo. |

## Blast radius

| Control | Status | Evidence |
| --- | --- | --- |
| Per-instance scoped tokens; one compromise is contained | ✅ met | Each instance gets its own minted token, matched by stored hash and scoped to its own routes; it cannot act as another instance or as an operator. PR #381. |

## Residual gaps (tracked, post-slice)

- ⛔ **HA / replication** of the panel + the central stores (single sqlite today) — post-GA.
- ⛔ **Multi-tenant** (`org` / `team`) isolation — post-GA.
- 🔧 The three live instances (Minder / Sterling / vedanta) still need to be **enrolled**
  and their `server.auth` set — an operator rollout step, not a code gap.

## Sign-off

Code-side controls are **met**; the remaining boxes are operator-configuration
(🔧) or explicitly post-GA (⛔). A deployment is GA-ready when every 🔧 row is
satisfied for that environment and a reviewer signs below.

- Reviewer: ______________________  Date: __________  Environment: __________
