# 12 — Phase 2: Auth design (+ `server.auth` spec)

Builds on [02 §Auth](02-serve-api.md), [05](05-identity-governance-iam.md), and the Phase 1
architecture ([11](11-architecture.md)). Designs authentication/authorization for a fleet where the
control plane reaches instances over the network and triggers runs across them. **The auth seam
already exists** — this phase *hardens, schematizes, and tiers it* rather than building it. It also
folds in the concrete **`server.auth` schematization spec** (per the locked plan).

## 1. Threat model

Once `serve` leaves loopback it is a remote control plane: `POST /run/{topology}` executes
topologies, `/api/*` mutates artifacts (legislative), `/jobs|/usage` leak outputs + cost. Threats:
unauthenticated access (the **open-by-default** footgun: `NoneAuthProvider` + CLI `--host 0.0.0.0`),
token theft/replay, over-broad tokens (a read token that can trigger runs), a compromised panel
escalating to reserved-for-human actions, and webhook spoofing (already mitigated by HMAC, but
orthogonal to identity).

## 2. Three identity layers (do not conflate)

| Layer | Question | Mechanism today | Phase 2 |
|---|---|---|---|
| **Transport authn** | May this *caller* hit this serve route? | `auth/` `AuthProvider` (`authenticate`/`authorize`), `auth_middleware` | harden: schematize, tier, enforce per-route |
| **Governance / human identity** | Whose *approval* counts; is this human allowed a reserved action? | `identity.provider` (`builtin`/`oidc`/…), HITL ([05](05-identity-governance-iam.md)) | unchanged; panel never gets reserved scopes |
| **Agent identity** | Which *agent* is acting (DIDs)? | AGT `AgentIdentity` | unchanged |

Transport scopes (`serve:*`, below) are a **separate namespace** from IAM/governance scopes
(`repo:read`, `skills:activate`, …). A transport token can never carry a governance scope, so it
**structurally cannot grant** reserved-for-human capabilities.

## 3. Two auth edges

- **panel → instance (machine-to-machine):** bearer token via `APIKeyAuthProvider`, or a service
  **JWT** via `JWTAuthProvider`. **Tiered** (§4). This is the connector credential ([11](11-architecture.md) §4 enrollment).
- **human → panel (user login):** **OIDC** — the panel is the OIDC relying party. Note serve's
  `JWTAuthProvider` is already OIDC-compliant (RS256/ES256 + JWKS auto-discovery), so the same
  pattern serves both a human-bearing JWT and a machine JWT. The panel maps the OIDC subject →
  its own session + the per-instance tokens it holds.

## 4. Transport scope tiers (new convention)

Three tiers, expressed as transport scopes; a tier is a scope bundle:

| Tier | Scopes | Grants | Routes |
|---|---|---|---|
| `read` | `serve:read` | observe | GET `/topologies` `/skills` `/archetypes` `/validate` `/triggers` `/usage*` `/jobs*` `/canary`, GET `/api/*/{id}[/yaml]`, GET `/conversations*` |
| `run` | `serve:read`,`serve:run` | observe + execute | + POST `/run/{t}`, POST `/hooks/{t}`, POST `/conversations`, POST `/conversations/{id}/messages` |
| `admin` | + `serve:admin` | + mutate artifacts/rollout | + PUT/POST/DELETE `/api/*`, POST `/api/reload`, POST `/canary/{t}/promote\|rollback` |

`GET /health` stays auth-exempt. **Artifact mutation (`serve:admin`) is legislative** — it must
additionally be a governed, audited, human-gated action ([11](11-architecture.md) §6), not an
ambient panel power.

## 5. `server.auth` schematization spec (the folded-in piece)

The CLI already reads `server.auth` (`cli/__init__.py:_build_auth_provider`) but it is **absent
from `workspace.schema.json`** — undocumented, unvalidated. Formalize the existing shape and extend
it with `tier` + `require_on_nonloopback`. Target block (add to the workspace `server` object,
following schema-change discipline — source schema + bundled copy + codegen + fixtures):

```yaml
server:
  auth:
    provider: api_key            # none (default) | api_key | jwt
    require_on_nonloopback: true # default-secure: refuse provider:none on a non-loopback bind
    config:
      # provider: api_key
      keys:
        - key_ref: env:PANEL_TOKEN   # ref only (env:VAR or SecretsProvider); never a literal
          client_id: control-plane
          client_name: Fleet control plane
          tier: run                  # read | run | admin  (expands to serve:* scopes)
          # scopes: [serve:read, serve:run]   # explicit alternative to tier
      # provider: jwt (OIDC)
      # issuer: https://issuer.example.com
      # audience: swarmkit
      # jwks_url: https://issuer/.well-known/jwks.json   # optional; derived from issuer
      # scopes_claim: scope
```

JSON-Schema shape: `provider` enum `["none","api_key","jwt"]` (default `none`);
`require_on_nonloopback` bool (default true); `config` validated per provider via `if/then`
(`api_key` → `config.keys` required, each `{key_ref, client_id, client_name?, tier? | scopes?}`;
`jwt` → `config.issuer` required + `audience`/`jwks_url`/`scopes_claim`). `key_ref` must be a
reference (`env:…` or a `credentials` entry), never a literal — enforced by convention + a lint
note. `tier` expands to `serve:*` scopes at load; `tier` and `scopes` are mutually exclusive.

### Behavioural changes (beyond the schema)

1. **Per-route authorization is wired.** Today `auth_middleware` calls `authenticate` but **not**
   `authorize` per route ([02](02-serve-api.md)); scopes are extracted but unenforced. Add a
   route→required-scope map (§4) and enforce it (middleware calls `authorize(identity, route,
   action)` or checks `required_scope ∈ identity.scopes`), returning `403` on insufficient scope.
2. **Default-secure bind.** When the effective bind is non-loopback (`--host` ≠ 127.0.0.1/::1) and
   `provider == none`, `create_app`/`swarmkit serve` **refuses to start** (or requires an explicit
   `--insecure` override) unless `require_on_nonloopback: false`. Loopback stays `none`-OK
   (backward-compatible — Minder on `127.0.0.1:8321` is unaffected).
3. **Reserved-scope guard.** Validate that no transport key/JWT is configured with a governance
   reserved scope (`skills:activate`, `mcp_servers:deploy`, `topologies:modify`, `iam:modify`,
   `audit:*`). Since transport scopes are the `serve:*` namespace this is belt-and-suspenders, but
   it makes the boundary explicit and fails loud on misconfiguration.

## 6. Token lifecycle

- **Issue / enroll:** the panel mints a per-instance token; the operator configures it on the
  instance as `key_ref: env:…` (or a `credentials` entry). One token per (instance, tier).
- **Rotate:** multiple active keys allowed → add new, switch panel, remove old (zero-downtime).
- **Revoke:** remove the key entry + reload (`POST /api/reload`) or restart.
- **Store:** values live in env/`SecretsProvider`, never in `workspace.yaml`. The panel stores a
  reference + metadata, not the secret ([03](03-provider-seams.md), [11](11-architecture.md) §6).

## 7. Separation-of-powers preservation

- The panel is **executive/media** ([05](05-identity-governance-iam.md)). `serve:admin` lets it
  *manage artifacts/rollout*, but every mutating call is **audited with `client_id`** and artifact
  push remains a **human-gated** action (legislative change) — the token grants the mechanism, not
  the authority to skip the gate.
- Transport authn never substitutes for governance: a `serve:run` token triggering a run still goes
  through `evaluate_action` and decision skills on the instance.
- Audit: extend audit events with the acting transport `client_id` (the AuditEvent already has a
  generic `payload`; add `client_id` there or as a field) so remote actions are attributable.

## 8. Backward compatibility

- Default stays `provider: none`; loopback deployments unchanged.
- Only a **non-loopback bind** newly requires a provider (default-secure) — a deliberate breaking
  change for anyone currently running serve open on `0.0.0.0`; documented + overridable via
  `require_on_nonloopback: false` / `--insecure`.

## 9. What Phase 3 implements (code)

1. **Schema:** add `server.auth` to `workspace.schema.json` + bundled copy + pydantic/TS codegen +
   valid/invalid fixtures (incl. `if/then` per provider) — full schema-change discipline.
2. **Tiers:** `tier → serve:* scopes` expansion in `_build_auth_provider`; mutual-exclusion check.
3. **Enforcement:** route→required-scope map; wire `authorize` in `auth_middleware` (→ `403`).
4. **Default-secure:** non-loopback + `none` guard in `create_app`/`serve` (+ `--insecure`).
5. **Reserved-scope guard** + **audit `client_id`** plumbing.
6. **Tests** (unit: tier expansion, route enforcement, default-secure guard; integration: api_key +
   jwt happy/deny paths) and **docs**.
7. **(Connector overlap)** `GET /capabilities` + heartbeat land in the connector phase but are
   gated by this auth.

This closes the [02 §Auth](02-serve-api.md) gaps and unblocks every multi-instance phase.
