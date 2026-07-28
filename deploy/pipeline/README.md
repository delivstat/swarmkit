# Bundled pipeline orchestrator

`serve` + the bundled reference **orchestrator**, over one shared workspace — the out-of-the-box way
to actually run pipelines. Without a driver, `serve` alone only enqueues pipeline events to the
durable saga store and 503s the drive seam; this compose adds the process that drives them.

See the design note: `design/details/bundled-pipeline-orchestrator.md`.

## Run it

```bash
docker compose -f deploy/pipeline/docker-compose.yml up -d --build
```

Two services come up sharing `{workspace}/.swarmkit/store.sqlite`:

- **serve** (`:8000`) — HTTP API + workspace UI. Authorizes, audits, enqueues pipeline events, and
  executes each stage's topology on demand (`POST /pipelines/run-stage`).
- **orchestrator** — claims events from the durable store and drives each saga, calling serve's
  run-stage seam. The only importer of the reference controller; durable, so a restart resumes
  mid-saga.

## Dispatch and inspect

Every pipeline is driven by events. Dispatch from the CLI (or the serve UI):

```bash
# start a run on a stage-graph
docker compose -f deploy/pipeline/docker-compose.yml exec serve \
  swarmkit pipeline emit <stage-graph-id> -w /workspace --tag demo

# list + search runs (by correlation id), inspect one
docker compose -f deploy/pipeline/docker-compose.yml exec serve \
  swarmkit pipeline sagas -w /workspace
docker compose -f deploy/pipeline/docker-compose.yml exec serve \
  swarmkit pipeline status <correlation-id> -w /workspace

# a parked funnel gate: approve (advance) or reject (skip)
docker compose -f deploy/pipeline/docker-compose.yml exec serve \
  swarmkit pipeline advance <correlation-id> <stage> -w /workspace
```

Or open **http://localhost:8000/ → Runs**: search runs by correlation id, replay a run over its
StageGraph on a read-only canvas, and inspect each node's timeline + produced artifact.

## Swap in Temporal

For weeks-long sagas or heavy fan-out, use Temporal as the durable engine instead of the bundled
orchestrator: comment out the `orchestrator` service, uncomment the `temporal` block, and repoint
the workspace's pipeline **signal sink** at the Temporal adapter. `serve` is unchanged — it only
enqueues/authorizes; the engine behind the sink is a deployment choice
(`design/details/orchestration-provider-seam.md`).
