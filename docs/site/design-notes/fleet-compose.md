---
status: draft
---

# Reproducible fleet — one `docker compose up`

## Goal

A tracked `deploy/fleet/` that brings up the **entire** demo fleet — observability + control-plane
panel + fleet UI + example instances, **enrolled** — from a clean checkout with one command and no
host toolchain beyond Docker. Today that stack lives only in untracked host scripts (`~/fleet-demo`),
so a fresh clone can't reproduce what we demo.

## Non-goals

- Not production hardening — the panel runs `--insecure-no-auth` (trial mode), same as
  `deploy/control-plane`. Production auth/vault is documented there and out of scope here.
- Not a new runtime/panel/UI feature — pure assembly of existing images + the enrollment handshake.
- Not every example — instances use self-contained workspaces (e.g. `hello-swarm`); ones needing
  external services (minder → Frigate/HA) are excluded.

## Shape

`deploy/fleet/docker-compose.yml`:

- **`include: ../observability/docker-compose.yml`** — reuse the obs bundle verbatim (collector,
  Jaeger, Prometheus, Grafana + the SwarmKit dashboard). Instances export to `otel-collector:4318`
  over the shared network; the browser reaches Jaeger `:16686` / Grafana `:3001`.
- **panel + ui + proxy** — reuse the `deploy/control-plane` images + Caddyfile (single-origin: UI at
  `:8080`, `/api` → panel; no CORS, no baked URL). The panel is started with
  `--jaeger-url`/`--grafana-url`/`--collector-endpoint` so the UI's observability card + per-run
  "View in Jaeger" deep-links resolve (this also closes the "pin the Jaeger base" gap).
- **instances** — the root runtime image (`Dockerfile`, ships runtime 1.56.0), each bind-mounting a
  self-contained example workspace at `/workspace` and exporting OTLP to the collector. Instance
  state (`.swarmkit/`, gitignored) is written into the mounted workspace.
- **enroll** — a one-shot container (runtime image) that runs the design-19 handshake once the panel
  + instances are healthy: `POST /instances` (endpoint = the instance's in-network URL
  `http://instance-<name>:8000`), mint a one-time token with `swarmkit fleet enroll-token`, then
  `POST /instances/{id}/register`. It **bind-mounts the same workspace dirs** as the instances, so
  the CLI mints tokens against the identical `{workspace}/.swarmkit/fleet.sqlite` the instance
  serves — the shared file is how the two containers agree on one fleet identity without a socket.

`.env.example` — `OPENROUTER_API_KEY` (for instance runs) + `SWARMKIT_CONTROL_PLANE_SECRET_KEY` (a
fixed Fernet key so the panel survives restarts instead of crashing on its own encrypted identity).

## Why the enrollment works cross-container

`swarmkit serve` and `swarmkit fleet enroll-token` both key off `{workspace}/.swarmkit/fleet.sqlite`.
Mount the same host workspace dir into both the instance and the enroll container and they share that
file → the token the enroll container mints matches the identity the instance presents at register.
`enroll` waits on the instances' health so the identity exists before it mints.

## Test / demo plan

- `docker compose -f deploy/fleet/docker-compose.yml config` validates + interpolates.
- `up -d` → all services healthy; `enroll` exits 0; `GET :8080/api/instances` lists the enrolled
  instances with `reachable` state; a run on an instance produces a Jaeger trace **and**
  `swarmkit_runs_total{service_name=…}` in Prometheus → the Grafana dashboard populates.
- README documents `up` / `down` / where each UI lives.

## Relationship to `~/fleet-demo`

This supersedes the untracked host scripts for reproducibility. The host scripts remain a valid
"run against your editable checkout" workflow (live code, no rebuild); the compose is the
"clone-and-run" path.
