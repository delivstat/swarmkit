# Securing `swarmkit serve`

`swarmkit serve` exposes a control API (run topologies, read jobs/usage, edit artifacts). It ships
an auth seam with three providers, **off by default** (open). This guide is the operator reference.

> **Default-secure:** binding a non-loopback host (`--host` other than `127.0.0.1`/`::1`) with the
> `none` provider **refuses to start**. Keep it loopback-only, configure auth, or override with
> `--insecure` / `server.auth.require_on_nonloopback: false`.

## Providers

Configure under `server.auth` in `workspace.yaml`:

| provider | use |
|---|---|
| `none` (default) | open access — only safe on loopback / a trusted network |
| `api_key` | static bearer tokens from a key registry (machine-to-machine) |
| `jwt` | OIDC-compliant JWT bearer tokens (RS256/ES256 + JWKS) — humans/SSO |

## Scope tiers

Transport scopes (`serve:*`) gate which routes a caller may hit. A key's **tier** expands to them:

| tier | scopes | can |
|---|---|---|
| `read` | `serve:read` | GET everything (observe) |
| `run` | `serve:read`, `serve:run` | + trigger runs, webhooks, conversations |
| `admin` | `+ serve:admin` | + edit/deploy artifacts (`/api/*`), canary promote/rollback |

`GET /health` is always unauthenticated. Transport scopes are separate from governance/IAM scopes —
a token **cannot** carry a reserved-for-human scope (`skills:activate`, `topologies:modify`, …).

## API keys

### Mint a token

```bash
swarmkit auth token control-plane --tier run
```

prints (once) a generated secret, the `export` line, and the YAML to paste:

```yaml
server:
  auth:
    provider: api_key
    config:
      keys:
        - key_ref: env:CONTROL_PLANE_TOKEN   # a reference, never the literal
          client_id: control-plane
          client_name: control-plane
          tier: run
```

Then `export CONTROL_PLANE_TOKEN=…`, restart serve, and callers send
`Authorization: Bearer <secret>`.

### `key_ref` schemes

- `env:VAR` — environment variable (recommended).
- `file:/path` — file contents (Docker/k8s mounted secrets; a Vault-agent-rendered file).
- `credentials:NAME` — a workspace `credentials` entry. `env` and `file` sources resolve; native
  cloud/vault backends (hashicorp-vault, aws/gcp/azure, plugin) raise a clear error until a
  SecretsProvider is wired — use `env:`/`file:` (e.g. point Vault Agent at a file).
- a literal string — works, but discouraged (it would sit in `workspace.yaml`).

### Rotate / revoke

Auth is read at **startup**, so changes need a `serve` restart (not `/api/reload`).

- **Rotate:** add a new key (new env var), switch callers, remove the old entry, restart. Multiple
  keys are valid at once → zero-downtime rotation.
- **Revoke:** remove the entry (or unset its env var), restart.

## JWT / OIDC

```yaml
server:
  auth:
    provider: jwt
    config:
      issuer: https://issuer.example.com
      audience: swarmkit          # default
      jwks_url: https://issuer.example.com/.well-known/jwks.json   # optional; derived from issuer
      scopes_claim: scope         # claim holding serve:* scopes
```

The token's `scope` claim must contain the `serve:*` scopes the routes require.

## Audit

Authenticated **mutating** calls (run/admin) are recorded in the serve access log
(`.swarmkit/store.sqlite`, table `serve_access`): `client_id`, provider, method, path, action,
status, timestamp — an attributable record of who did what over the API.

## Quick recipes

- **Local only (default):** no config; serve binds `127.0.0.1` open. Fine for one box (e.g. Minder).
- **Remote, machine clients:** `provider: api_key`, mint a `run` (or `admin`) token, bind your
  interface, restart.
- **Remote, human SSO:** `provider: jwt` against your IdP; map users to `serve:*` via the scope claim.
