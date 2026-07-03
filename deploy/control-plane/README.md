# SwarmKit control-plane — deploy + operate

Packaging and the operator runbook for self-hosting the fleet control plane (Phase 8,
design [18](../../design/details/control-plane/18-hardening-rollout.md)). The panel is a
self-contained FastAPI service; the fleet UI is a static Next.js frontend built against it.

## Run the panel

```bash
docker compose -f deploy/control-plane/docker-compose.yml up -d --build
curl localhost:8800/health         # {"status":"ok"}
```

The central stores (registry / aggregation / artifacts / proposals — sqlite in WAL mode)
persist in the `control-plane-data` volume.

> **The panel runs OPEN (no auth) until you configure a principal.** For anything but a
> loopback dev box, set operator tokens and/or OIDC (see below) before exposing it.

## Configure auth (production)

Uncomment `command:` in the compose file:

- `--operator-token=<secret>` — bearer token with full operator access (repeatable).
- `--oidc-issuer=<url> --oidc-audience=<aud>` — verify human OIDC JWTs (the UI login path).
- `--cors-origin=<https://fleet.example.com>` — the exact UI origin (no `*` in production).

Put the panel behind TLS (reverse proxy). Tokens are secrets — inject via env, never commit.

## The fleet UI

The compose bundle builds and runs the UI (`swarmkit-control-plane-ui`, a Next.js
standalone image) alongside the panel — up on `:3000`.

The UI reaches the panel via `NEXT_PUBLIC_CONTROL_PLANE_API`, which Next **inlines at
build time**. Two ways to wire it:

- **Same origin (recommended).** Leave it empty (the default) and put a reverse proxy in
  front so `/` serves the UI and the panel's routes are reachable at the same origin. No
  CORS, no baked URL, and OIDC redirect URIs stay stable.
- **Baked URL.** Set the `NEXT_PUBLIC_CONTROL_PLANE_API` build arg (compose
  `ui.build.args`) to the panel's public URL. Then set the panel's `--cors-origin` to the
  UI's origin, since the browser now makes cross-origin calls.

OIDC login (optional): also set `NEXT_PUBLIC_OIDC_AUTHORITY` / `NEXT_PUBLIC_OIDC_CLIENT_ID`
at build. Serve both behind TLS.

## Runbook

**Enroll an instance**
- **Mode A (directly reachable):** `POST /instances {name, endpoint, connection:"direct", token_ref}` — the panel verifies by pulling `/capabilities`. Full control.
- **Mode B (NAT'd / loopback, e.g. Minder):** enroll `connection:"poll"`, mint a token (instance detail page), and run `swarmkit connect` alongside the instance (outbound-only). The panel drives it through the command queue.

**Rotate / revoke a token** — re-mint on the instance detail page (old token stops working); to revoke, delete + re-enroll or rotate the instance's `server.auth`.

**Recover an unreachable instance** — Mode A shows `unreachable`; fix reachability/token and hit `POST /instances/{id}/verify`. Mode B recovers on the next poll.

**Roll back a bad deploy** — set the intended version back on the instance's Deployments card (drift then re-clears), or `POST /instances/{id}/deploy` a known-good registry version. Deploys are human-gated + audited.

**Read the audit trail** — `GET /audit` (fleet) or the Runs page; every panel-triggered mutation carries the acting principal + instance.

**Backup / restore** — snapshot the `control-plane-data` volume (it holds all four sqlite stores). Restore by replacing the volume contents before start. WAL checkpoints on clean shutdown.

## Breaking change (serve default-secure)

SwarmKit serve now **refuses to start on a non-loopback bind with `provider: none`**. When
enrolling an instance that currently binds `0.0.0.0` open, set `server.auth` (a token or
OIDC) first, or use the `--insecure` / `require_on_nonloopback: false` escape hatch. Call
this out in your own release notes.

## Security checklist (pre-GA)

The full control-by-control review (implementation + evidence per control) is in
[SECURITY-REVIEW.md](SECURITY-REVIEW.md) — the GA gate. The operator-configured rows:

- [ ] operator tokens and/or OIDC configured (panel not open)
- [ ] `--cors-origin` set to the exact UI origin (no `*`)
- [ ] TLS on human↔panel and panel↔instance
- [ ] per-instance scoped tokens (blast radius contained); tokens are refs/secrets, never committed
- [ ] artifact deploy + proposal approval remain human-gated (they are, structurally) and audited

## OSS single-instance vs fleet panel

The single-box dashboard shipped with `swarmkit serve` (design 09) stays the OSS on-ramp.
This control plane is the **separate** app for operating *many* instances — don't point it
at a single box expecting the serve dashboard's per-run UX.

## Deferred (later Phase 8)

Not in this bundle: the git/Postgres central-store swap (sqlite is the current store);
HA/replication of the panel; multi-tenant (`org`/`team`) hardening. Tracked in
`design/details/control-plane/IMPLEMENTATION.md`.
