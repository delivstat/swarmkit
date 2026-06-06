# Serve mode

`swarmkit serve` starts a persistent HTTP server for production workloads.

## Quick start

```bash
swarmkit serve ./workspace --host 0.0.0.0 --port 8000
```

## Endpoints

### Jobs

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/run` | Submit a topology run (async) |
| `GET` | `/jobs/{id}` | Poll job status |
| `GET` | `/jobs/history` | List persisted jobs (survives restart) |

### Conversations

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/conversations` | Create a new conversation |
| `GET` | `/conversations` | List conversations |
| `GET` | `/conversations/{id}` | Load full conversation history |
| `POST` | `/conversations/{id}/messages` | Send message (SSE streaming) |

### CRUD (topologies, skills, archetypes)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/topologies` | List topologies |
| `GET` | `/api/topologies/{id}` | Get topology details |
| `GET` | `/api/topologies/{id}/yaml` | Get raw YAML |
| `PUT` | `/api/topologies/{id}/yaml` | Update YAML |
| `POST` | `/api/topologies` | Create new topology |
| `DELETE` | `/api/topologies/{id}` | Delete topology |

Same pattern for `/api/skills` and `/api/archetypes`.

### Usage tracking

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/usage` | Global token usage summary |
| `GET` | `/usage/{job_id}` | Per-job usage breakdown |

### MCP endpoint

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/mcp` | Streamable HTTP MCP endpoint |

Each topology becomes an MCP tool. External agents can call your swarm topologies via standard MCP protocol.

### Webhooks

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhooks/{trigger_id}` | Fire a webhook trigger |

Webhook signatures are validated with HMAC-SHA256 when `secret` is configured on the trigger.

### Canary

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/canary` | List canary routes and metrics |
| `POST` | `/canary/{topology}/promote` | Promote canary version |
| `POST` | `/canary/{topology}/rollback` | Rollback canary version |

## Authentication

Configure in `workspace.yaml`:

```yaml
server:
  auth:
    provider: jwt
    jwks_url: https://your-idp/.well-known/jwks.json
    audience: swarmkit
    issuer: https://your-idp/
```

Available providers:

| Provider | Description |
|----------|-------------|
| `none` | No authentication (default) |
| `api_key` | Static API key via `SWARMKIT_API_KEY` env var |
| `jwt` | JWT with JWKS auto-discovery |

## Server configuration

```yaml
server:
  host: "0.0.0.0"
  port: 8000
  jobs:
    max_concurrent: 5
    timeout_seconds: 300
  mcp:
    enabled: true
```

## Triggers

Cron and webhook triggers are defined in `triggers/*.yaml`:

```yaml
apiVersion: swarmkit/v1
kind: Trigger
metadata:
  id: nightly-review
  name: Nightly Code Review
type: cron
schedule: "0 2 * * *"
topology: code-review
input: "Review all PRs opened today"
```

```yaml
apiVersion: swarmkit/v1
kind: Trigger
metadata:
  id: pr-webhook
  name: PR Webhook
type: webhook
topology: code-review
secret: ${WEBHOOK_SECRET}
```

## Docker

```bash
docker run -v ./workspace:/workspace \
  -e OPENROUTER_API_KEY=$OPENROUTER_API_KEY \
  -p 8000:8000 \
  ghcr.io/delivstat/swarmkit:latest
```
