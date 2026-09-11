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

#### Attachments

A caller that already holds a file — a snapshot, an uploaded photo — passes it beside the input
instead of making an agent go and fetch it:

```json
POST /run/describe-scene
{
  "input": "What is at the gate?",
  "attachments": [
    { "path": "snapshots/gate-1732.jpg" }
  ]
}
```

| Field | | |
|---|---|---|
| `path` | workspace-relative | **exactly one of** `path` / `data` |
| `data` | base64 | for a caller holding bytes rather than a file |
| `name` | optional | display/filename; derived from `path` when absent |
| `handling` | `preprocess` (default) or `native` | intent for non-image types; inert today |

**There is no `type` field.** The media type is read from the file's content — sending one is a
422, because a caller's claim about bytes that are about to be forwarded to a model is not evidence.
`url` and `stream` sources are refused for the same reason and a related one: the runtime does not
fetch caller-supplied addresses, and an attachment is re-read on every turn of a tool loop, so it
has to be re-readable.

Attachments reach the **entry agent's first message and no downstream node**. An agent that wants a
file it was not handed asks for one through a skill.

A bad path is a **422 on this request**, not a job that fails a moment later — so a job id means the
file was readable. Only images are carried today (`image/png`, `image/jpeg`, `image/gif`,
`image/webp`); anything else is refused by name. Per-attachment ceiling is 20 MB
(`SWARMKIT_ATTACHMENT_MAX_BYTES`).

Every attachment is written to the audit log as a `run.attachments` event carrying name, media type,
size, SHA-256 and source path — **never the bytes**. The digest is what makes the reference
checkable later; storing content would put arbitrary material into a log meant to stay readable.

The CLI equivalent is `swarmkit run <ws> <topology> --attach <path>` (repeatable).

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
