# Level 11: Serve & HTTP API

Run your workspace as a persistent HTTP service — jobs, conversations, SSE streaming, and authentication.

## What you'll learn

- `swarmkit serve` — persistent HTTP server
- REST API endpoints
- SSE streaming for real-time progress
- Authentication (API key, JWT)
- Conversation API
- CRUD for topologies, skills, archetypes
- Job concurrency and timeouts
- Usage tracking

## Start the server

```bash
swarmkit serve . --port 8000
```

The server stays running, accepts HTTP requests, and manages MCP server lifecycle.

## API endpoints

### Health and introspection

```bash
# Health check
curl http://localhost:8000/health

# List topologies
curl http://localhost:8000/topologies

# List skills
curl http://localhost:8000/skills

# List archetypes
curl http://localhost:8000/archetypes

# Validate workspace
curl http://localhost:8000/validate
```

### Submit a job

```bash
# One-shot execution (async)
curl -X POST http://localhost:8000/run/hello \
  -H "Content-Type: application/json" \
  -d '{"input": "Hello world!"}'

# Returns: {"job_id": "abc123", "status": "pending"}
```

### Poll job status

```bash
curl http://localhost:8000/jobs/abc123

# Returns: {"job_id": "abc123", "status": "completed", "output": "..."}
```

### SSE streaming

```bash
# Stream real-time progress events
curl -N http://localhost:8000/jobs/abc123/stream

# Events:
# data: {"type": "progress", "text": "[coordinator] thinking..."}
# data: {"type": "progress", "text": "[researcher] calling brain-search"}
# data: {"type": "done", "output": "...", "usage": {...}, "trace": {...}}
```

### Conversations

```bash
# Create a conversation
curl -X POST http://localhost:8000/conversations \
  -H "Content-Type: application/json" \
  -d '{"topology": "hello"}'

# List conversations
curl http://localhost:8000/conversations

# Load a conversation
curl http://localhost:8000/conversations/conv123

# Send a message (SSE streaming response)
curl -X POST http://localhost:8000/conversations/conv123/messages \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me about karma"}'
```

### CRUD API

```bash
# Get topology YAML
curl http://localhost:8000/api/topologies/hello/yaml

# Update topology YAML
curl -X PUT http://localhost:8000/api/topologies/hello/yaml \
  -H "Content-Type: application/json" \
  -d '{"yaml": "apiVersion: swarmkit/v1\nkind: Topology\n..."}'

# Create new topology
curl -X POST http://localhost:8000/api/topologies \
  -H "Content-Type: application/json" \
  -d '{"id": "new-topology", "yaml": "..."}'

# Delete topology
curl -X DELETE http://localhost:8000/api/topologies/hello

# Same pattern for /api/skills and /api/archetypes
```

### Usage tracking

```bash
# Global usage summary
curl http://localhost:8000/usage

# Per-job usage
curl http://localhost:8000/usage/abc123

# Job history (persisted across restarts)
curl http://localhost:8000/jobs/history
```

## Authentication

### API key

Mint one — it prints the config to paste, never the secret into a file:

```bash
swarmkit auth token control-plane --tier run --client-name "Fleet panel" --env-var PANEL_TOKEN
```

```yaml
# workspace.yaml
server:
  auth:
    provider: api_key
    config:
      keys:
        - key_ref: env:PANEL_TOKEN      # a reference, never the literal
          client_id: control-plane
          client_name: Fleet panel
          tier: run                     # read | run | admin — or explicit `scopes`
```

```bash
export PANEL_TOKEN=…                    # the secret the command generated
swarmkit serve .
curl -H "Authorization: Bearer $PANEL_TOKEN" http://localhost:8000/topologies
curl http://localhost:8000/auth-info    # public: which login gate a client should render
curl -H "Authorization: Bearer $PANEL_TOKEN" http://localhost:8000/whoami   # the authenticated caller, its scopes
```

Every `run`/`admin` call is audited with the acting `client_id`. A non-loopback bind with
`provider: none` refuses to start unless you opt in (`--insecure`), so an unauthenticated serve
cannot be exposed by accident.

### JWT with JWKS auto-discovery

```yaml
server:
  auth:
    provider: jwt
    config:
      issuer: https://your-idp/
      audience: swarmkit
      jwks_url: https://your-idp/.well-known/jwks.json   # optional; discovered from the issuer otherwise
      scopes_claim: scope                                 # which claim carries the tiers
```

Tokens are validated against the JWKS endpoint. No secrets to manage — just point to your identity provider.

## Server configuration

```yaml
server:
  host: "0.0.0.0"
  port: 8000
  jobs:
    max_concurrent: 5        # max simultaneous topology runs
    timeout_seconds: 300     # per-job timeout
  mcp:
    enabled: true            # expose MCP endpoint at /mcp
```

## MCP endpoint

Each topology becomes an MCP tool accessible at `/mcp`:

```bash
# Any MCP client can connect:
# POST http://localhost:8000/mcp
# Tool: run_hello(input: "...")
# Tool: run_content_team(input: "...")
```

This lets external AI agents (Claude Desktop, Cursor) call your topologies as tools.

## Your workspace so far

```
my-swarm/
├── workspace.yaml          # server config, auth
└── ...                     # everything from previous levels
```

## Next

[Level 12: Triggers & Canary](12-triggers-canary.md) — schedule runs and safely roll out changes.
