# Level 22: Running a fleet

Many `swarmkit serve` instances under one self-hosted control plane: enrolment, observed state,
federated runs and gates, artifact registry, gap mining — and the telemetry that makes a fleet
readable.

## What you'll learn

- What the control plane is (and is not): a separate app *over* the serve contract, never a dependency of it
- Two ways an instance joins: Mode A (reachable endpoint) and Mode B (`swarmkit connect`, outbound-only)
- The fleet seam on every instance: `/capabilities`, `/fleet/state`, enrolment tokens, membership keys
- Federated runs and per-instance traces; harness gates resolved from the panel
- The artifact registry and human-gated deploys; gap mining across instances
- OpenTelemetry: traces to Jaeger, metrics to Prometheus, the Grafana dashboard

## The idea

One instance is the OSS on-ramp: `swarmkit serve` with its portal. Operating *many* — a Minder box
in every house, a serve per team — needs a place that sees them all, and that place must not become
a control plane you hand to a vendor. So it is a second self-hostable package,
`swarmkit-control-plane`, an independent application and a client of the serve API: it depends on
the contract, never on the runtime's code ([Fleet control plane](../design-notes/fleet-control-plane.md)).

Each instance keeps its own authority. The panel can *ask* an instance to deploy a registry
version or resolve a gate; every such mutation is human-gated on the panel and audited with the
acting principal on both sides.

## Build it

### 1. Bring up the panel

```bash
docker compose -f deploy/control-plane/docker-compose.yml up -d --build
curl localhost:8080/api/health          # {"status":"ok"}
open http://localhost:8080              # the fleet UI
```

The trial default is `--insecure-no-auth`. Before exposing it: `--operator-token=<secret>` (bearer,
repeatable) and/or `--oidc-issuer` + `--oidc-audience` for human logins, and a fixed
`SWARMKIT_CONTROL_PLANE_SECRET_KEY` — the fleet identity is encrypted with it, and an ephemeral key
means the panel cannot read its own registry after a restart.

### 2. Enrol an instance

**Mode A — the panel can reach the instance.** On the instance, mint an enrolment token (an admin
action; this is what makes a `manage`-scope join human-issued):

```bash
swarmkit fleet enroll-token ./workspace --scope manage --ttl 900   # or POST /fleet/enroll-token (serve:admin)
swarmkit fleet memberships ./workspace                             # who is registered, with what scope — no secrets
```

The panel registers with it (`POST /fleet/register`, its signed identity pinned), receives a
membership key, and from then on reads `/capabilities` and `/fleet/state` with that key — rotated
with `POST /fleet/refresh`, ejected with `DELETE /fleet/membership/{id}`.

**Mode B — the instance is NAT'd or on loopback** (a Minder box at home). Enrol it as
`connection: "poll"`, mint a token on its detail page, and run the connector next to it:

```bash
swarmkit connect https://fleet.example.com --join-code <code> --name house-3 \
  --serve-url http://127.0.0.1:8000 --tier run
```

Outbound-only; the panel drives it through a command queue, and the UI says "poll mode" where a
live operation is not possible rather than pretending.

### 3. What the panel sees

- **Instances** — capabilities (`serve_version`, topologies, providers, `features.auth/canary/a2a`),
  reachability, drift between intended and observed artifacts.
- **Runs** — every instance's runs in one list; a run's per-agent trace is fetched from the instance
  that owns it (`GET /instances/{id}/runs/{run_id}/trace`).
- **Gates** — relay and input-request gates from every instance, resolved from the panel.
- **Registry** — artifact versions by content hash; a deploy pushes a known version to an instance,
  human-gated and audited; a rollback is the same operation with an older version.
- **Gaps** — capability gaps mined across instances by occurrence, with propose → approve →
  distribute as the human-gated loop.

### 4. Make it observable

```bash
# each instance — env vars, or ~/.swarmkit/config.yaml for a persistent setting
export SWARMKIT_OTEL_EXPORTER=otlp
export SWARMKIT_OTEL_ENDPOINT=http://collector:4318/v1/traces
```

Traces (one per run, one span per agent and tool call) go to Jaeger; `swarmkit_runs_total`,
governance decisions and token counts to Prometheus; `deploy/observability` ships the collector and
a Grafana **SwarmKit Fleet** dashboard.

## Run it

The whole demo fleet — observability, control plane, three example instances, enrolled
automatically — is one compose file:

```bash
cp deploy/fleet/.env.example deploy/fleet/.env      # set SWARMKIT_CONTROL_PLANE_SECRET_KEY
docker compose -f deploy/fleet/docker-compose.yml up -d --build
curl -s http://localhost:8080/api/instances | python -m json.tool
```

| Service | URL |
|---|---|
| Fleet UI | http://localhost:8080 |
| Jaeger | http://localhost:16686 |
| Grafana | http://localhost:3001 |
| Prometheus | http://localhost:9090 |

Run a topology on an instance and watch it appear in the Runs page, as a trace in Jaeger, and as
`swarmkit_runs_total{service_name=…}` on the dashboard. Then:

```bash
just demo-fleet-identity     # the instance's signed identity, enrolment and key rotation
just demo-signed-deploy      # a registry deploy: signed, human-gated, audited on both sides
```

## What happened

- No instance handed over authority: the panel holds a membership key with a scope, and every
  mutation it triggers is a request the instance audits with the acting principal.
- A run on any instance is one run: correlated, traced, its gate resolvable from one inbox.
- Two SwarmKit instances talking A2A (Level 20) both report here, and their records join on the
  A2A `contextId` — which is where "one run rendered across instances" earns its name.

## Learn more

- [Fleet control plane](../design-notes/fleet-control-plane.md) · [Fleet compose](../design-notes/fleet-compose.md)
- `deploy/control-plane/README.md` — auth, runbook, backup; `deploy/fleet/README.md` — the demo fleet
- [Telemetry configuration](../reference/telemetry.md) · [Serve mode](../reference/serve.md) (the `/fleet/*` seam)
