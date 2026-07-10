# SwarmKit demo fleet — one `docker compose up`

The whole demo fleet from a clean checkout: **observability** (collector, Jaeger, Prometheus,
Grafana + the SwarmKit dashboard) + **control-plane** (panel + fleet UI behind a single-origin Caddy
proxy) + **example instances**, enrolled automatically.

This supersedes the untracked `~/fleet-demo` host scripts for reproducibility. The host scripts stay
useful for running against your editable checkout (live code, no image rebuild); this is the
clone-and-run path. Design: `design/details/fleet-compose.md`.

## Run

```bash
cp deploy/fleet/.env.example deploy/fleet/.env
# set SWARMKIT_CONTROL_PLANE_SECRET_KEY (generate one — see the file); OPENROUTER_API_KEY optional

docker compose -f deploy/fleet/docker-compose.yml up -d --build
```

First run builds three images (runtime, panel, UI) — a few minutes. The `enroll` job runs once the
panel + instances are healthy and exits 0.

| Service | URL |
| --- | --- |
| Fleet UI | http://localhost:8080 |
| Jaeger | http://localhost:16686 |
| Grafana | http://localhost:3001 (admin/admin, or anonymous) |
| Prometheus | http://localhost:9090 |

## Verify

```bash
# enrolled instances (through the proxy)
curl -s http://localhost:8080/api/instances | python -m json.tool

# enrollment log
docker compose -f deploy/fleet/docker-compose.yml logs enroll
```

Then run a topology on an instance and watch it flow through: a trace appears in Jaeger and
`swarmkit_runs_total{service_name=…}` in Prometheus, so the Grafana **SwarmKit Fleet** dashboard
populates.

## Down

```bash
docker compose -f deploy/fleet/docker-compose.yml down            # keep volumes
docker compose -f deploy/fleet/docker-compose.yml down -v         # wipe panel registry too
```

## Notes

- **Trial auth.** The panel runs `--insecure-no-auth` — anyone reaching it has full control. For
  production auth (operator token / OIDC) + vault-backed credential encryption, see
  `deploy/control-plane/`.
- **Instances.** `instance-hello` (hello-swarm) and `instance-sterling` (sterling-oms) bind-mount
  their example workspaces; add more by copying an instance block + an `enroll` entry in
  `FLEET_INSTANCES`. Workspaces needing external services (e.g. minder → Frigate/HA) aren't included.
- **Instance state** (`.swarmkit/`, gitignored) is written into the mounted workspace dirs.
