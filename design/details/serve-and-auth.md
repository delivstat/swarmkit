# Serve: async jobs, MCP endpoint, webhooks

Design ref: §14.1 (persistent/scheduled mode).

## Goal

Upgrade `swarmkit serve` from synchronous request-response to an async job model with SSE streaming, an MCP endpoint exposing topologies as tools, and webhook triggers.

## Non-goals

- Authentication/authorization (separate PR)
- Persistent job storage (in-memory only for now)
- Web UI integration

## API shape

### Async job execution

- `POST /run/{topology}` — starts job in background, returns `{job_id, status: "running"}`
- `GET /jobs/{job_id}` — poll job status/output
- `GET /jobs/{job_id}/stream` — SSE stream of progress events
- `GET /jobs` — list recent jobs

### MCP endpoint

- `/mcp` — Streamable HTTP transport
- Each topology becomes an MCP tool (`run_{name}`)
- Each topology becomes an MCP resource with description

### Webhooks

- `POST /hooks/{topology}` — accepts any JSON body, triggers async job

### Server improvements

- CORS middleware (configurable origins)
- Request logging middleware
- MCP servers started at boot via lifespan

## Test plan

- httpx AsyncClient + ASGITransport against FastAPI app
- Test job creation, polling, listing, webhooks, health, topologies

## Demo plan

- `swarmkit serve` in hello-swarm workspace
- curl examples in PR body showing async job flow
