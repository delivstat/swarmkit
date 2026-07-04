# SwarmKit control-plane — deploy + operate

Packaging and the operator runbook for self-hosting the fleet control plane (Phase 8,
design [18](../../design/details/control-plane/18-hardening-rollout.md)). The bundle is a
Caddy reverse proxy in front of the panel (FastAPI) and the fleet UI (Next.js), all on one
origin.

## Run it

```bash
docker compose -f deploy/control-plane/docker-compose.yml up -d --build
curl localhost:8080/api/health     # {"status":"ok"}
# then open http://localhost:8080  → the fleet UI
```

The proxy (`:8080`) serves the UI at `/` and forwards `/api/*` to the panel (prefix
stripped) — one origin, so the browser needs no CORS and no baked panel URL. The panel and
UI are internal (not published to the host). The central stores (registry / aggregation /
artifacts / proposals — sqlite in WAL mode) persist in the `control-plane-data` volume.

> **Trial default is insecure.** The panel runs with `--insecure-no-auth` so the bundle
> starts out of the box; anyone who can reach `:8080` has full control. Configure a
> principal before exposing it.

## Configure auth (production)

In the compose `panel.command`, comment out `--insecure-no-auth` and set instead:

- `--operator-token=<secret>` — bearer token with full operator access (repeatable).
- `--oidc-issuer=<url> --oidc-audience=<aud>` — verify human OIDC JWTs (the UI login path).

With auth configured the default-secure guard passes on its own. Put the proxy behind TLS;
tokens are secrets — inject via env, never commit. (OIDC login also needs
`NEXT_PUBLIC_OIDC_AUTHORITY` / `NEXT_PUBLIC_OIDC_CLIENT_ID` set as UI build args.)

The UI calls the panel at `/api` (baked build arg), which the proxy strips + forwards — so
there's no cross-origin call and no host-specific URL in the image.

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
