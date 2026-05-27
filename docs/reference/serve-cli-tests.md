# `swarmkit serve` — CLI Reference & Real Test Outputs

Live test run against the `hello-swarm` workspace. All outputs captured from
actual `curl` calls against a running server. Date: 2026-05-27, version 1.2.57.

---

## Starting the server

```bash
$ swarmkit serve examples/hello-swarm/workspace --port 8321
```

Server boots, prints banner, starts MCP servers and trigger scheduler:

```
INFO     MCP endpoint mounted at /mcp
INFO     Server config: max_concurrent=5, timeout=300s, mcp_enabled=True
INFO     MCP servers started at boot
INFO     TriggerScheduler started (poll_interval=30s, 0 trigger(s) configured)
INFO     Uvicorn running on http://0.0.0.0:8321
```

---

## Introspection endpoints

### GET /health

```bash
$ curl -s http://localhost:8321/health | jq .
```

```json
{
    "status": "ok",
    "workspace": "hello-swarm"
}
```

### GET /topologies

```bash
$ curl -s http://localhost:8321/topologies | jq .
```

```json
[
    "hello"
]
```

### GET /skills

```bash
$ curl -s http://localhost:8321/skills | jq .
```

```json
[
    {
        "id": "say-hello",
        "category": "capability"
    }
]
```

### GET /archetypes

```bash
$ curl -s http://localhost:8321/archetypes | jq .
```

```json
[
    "greeter"
]
```

### GET /validate

```bash
$ curl -s http://localhost:8321/validate | jq .
```

```json
{
    "valid": true,
    "workspace_id": "hello-swarm",
    "topologies": ["hello"],
    "skills": ["say-hello"],
    "archetypes": ["greeter"]
}
```

### GET /triggers

```bash
$ curl -s http://localhost:8321/triggers | jq .
```

```json
[]
```

Returns configured triggers. Empty for `hello-swarm` (no triggers configured).

---

## Job execution

### POST /run/{topology} — submit async job

```bash
$ curl -s -X POST http://localhost:8321/run/hello \
  -H "Content-Type: application/json" \
  -d '{"input": "Say hello to SwarmKit users", "max_steps": 5}' | jq .
```

```json
{
    "job_id": "4f376c35f0fa",
    "status": "running",
    "output": null,
    "error": null
}
```

### GET /jobs — list all jobs

```bash
$ curl -s http://localhost:8321/jobs | jq .
```

```json
[
    {
        "job_id": "4f376c35f0fa",
        "topology": "hello",
        "status": "completed",
        "created_at": "2026-05-27T08:52:44.238427+00:00",
        "completed_at": "2026-05-27T08:52:44.311730+00:00"
    }
]
```

### GET /jobs/{job_id} — poll specific job

```bash
$ curl -s http://localhost:8321/jobs/4f376c35f0fa | jq .
```

```json
{
    "job_id": "4f376c35f0fa",
    "status": "completed",
    "output": "mock response",
    "error": null
}
```

### GET /jobs/{job_id}/stream — SSE event stream

```bash
$ curl -s -N http://localhost:8321/jobs/$JOB_ID/stream
```

```
data: Job started for topology 'hello'

data: Job completed successfully

data: [done] status=completed
```

### Concurrent jobs

Three jobs submitted simultaneously — all complete in parallel:

```bash
$ for i in 1 2 3; do
    curl -s -X POST http://localhost:8321/run/hello \
      -H "Content-Type: application/json" \
      -d "{\"input\": \"concurrent job $i\"}" &
  done
  wait
```

```json
[
    {"job_id": "3f41a7847bc1", "topology": "hello", "status": "completed", ...},
    {"job_id": "b7217b21be91", "topology": "hello", "status": "completed", ...},
    {"job_id": "5ebd3bee0da8", "topology": "hello", "status": "completed", ...}
]
```

Concurrency is bounded by `server.jobs.max_concurrent` (default: 5). When all
slots are occupied, new requests get HTTP 429.

---

## Error handling

### POST /run/{nonexistent} — topology not found

```bash
$ curl -s -X POST http://localhost:8321/run/nonexistent \
  -H "Content-Type: application/json" \
  -d '{"input": "test"}' | jq .
```

```json
{
    "detail": "Topology 'nonexistent' not found. Available: ['hello']"
}
```

HTTP 404.

### GET /jobs/{nonexistent} — job not found

```bash
$ curl -s http://localhost:8321/jobs/doesnotexist | jq .
```

```json
{
    "detail": "Job 'doesnotexist' not found"
}
```

HTTP 404.

---

## Webhook triggers

### POST /hooks/{topology} — webhook endpoint

```bash
$ curl -s -X POST http://localhost:8321/hooks/hello \
  -H "Content-Type: application/json" \
  -d '{"input": "webhook triggered run"}' | jq .
```

```json
{
    "job_id": "733d561e1dd2",
    "status": "running",
    "output": null,
    "error": null
}
```

### HMAC-SHA256 signature validation

Webhook triggers with `auth.credentials_ref` validate signatures:

```python
from swarmkit_runtime.triggers._webhook import validate_webhook_signature
import hashlib, hmac

body = b'{"event": "push", "ref": "refs/heads/main"}'
secret = "my-webhook-secret"
digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
sig = f"sha256={digest}"

validate_webhook_signature(body, sig, secret)       # True
validate_webhook_signature(b"tampered", sig, secret) # False
validate_webhook_signature(body, sig, "wrong-secret")# False
validate_webhook_signature(body, digest, secret)     # False (missing prefix)
```

---

## Conversations (multi-turn)

### POST /conversations — create

```bash
$ curl -s -X POST http://localhost:8321/conversations \
  -H "Content-Type: application/json" \
  -d '{"topology": "hello"}' | jq .
```

```json
{
    "id": "66b0bd26",
    "topology": "hello"
}
```

### GET /conversations — list

```bash
$ curl -s http://localhost:8321/conversations | jq .
```

```json
[
    {"id": "66b0bd26", "topology": "hello", "turns": "0", ...},
    {"id": "d2976657", "topology": "hello", "turns": "10", ...}
]
```

### POST /conversations/{id}/messages — send message

```bash
$ curl -s -X POST http://localhost:8321/conversations/66b0bd26/messages \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello from the API"}' | jq .
```

```json
{
    "output": "mock response",
    "turns": 2,
    "conversation_id": "66b0bd26"
}
```

---

## Authentication (API key)

Start server with `APIKeyAuthProvider`:

```python
from swarmkit_runtime.server import create_app
from swarmkit_runtime.auth import APIKeyAuthProvider

auth = APIKeyAuthProvider(keys=[{
    "key_ref": "env:TEST_API_KEY",
    "client_id": "test-client",
    "client_name": "Test Client",
    "scopes": ["topologies:run", "jobs:read"],
}])
app = create_app(Path("examples/hello-swarm/workspace"), auth_provider=auth)
```

Or via `workspace.yaml`:

```yaml
server:
  auth:
    provider: api_key
    config:
      keys:
        - key_ref: "env:TEST_API_KEY"
          client_id: "test-client"
          client_name: "Test Client"
          scopes: ["topologies:run", "jobs:read"]
```

### Health — no auth needed

```bash
$ curl -s http://localhost:8322/health
{"status": "ok", "workspace": "hello-swarm"}
```

### No auth header — rejected (401)

```bash
$ curl -s -w "\nHTTP %{http_code}" http://localhost:8322/topologies
{"error":"Missing or invalid API key"}
HTTP 401
```

### Wrong key — rejected (401)

```bash
$ curl -s -w "\nHTTP %{http_code}" -H "Authorization: Bearer wrong-key" \
  http://localhost:8322/topologies
{"error":"Missing or invalid API key"}
HTTP 401
```

### Correct key — success (200)

```bash
$ curl -s -H "Authorization: Bearer sk-test-key-12345" \
  http://localhost:8322/topologies | jq .
["hello"]
```

### Authenticated job submission

```bash
$ curl -s -X POST http://localhost:8322/run/hello \
  -H "Authorization: Bearer sk-test-key-12345" \
  -H "Content-Type: application/json" \
  -d '{"input": "authenticated request"}' | jq .
```

```json
{
    "job_id": "f9ba58f4278c",
    "status": "running",
    "output": null,
    "error": null
}
```

---

## Trigger scheduler (cron)

```python
from swarmkit_runtime.triggers._scheduler import TriggerScheduler

async def fire_fn(topology: str, source: str):
    print(f"FIRED: topology={topology} source={source}")

triggers = [{
    "id": "every-minute",
    "type": "cron",
    "enabled": True,
    "targets": ["hello"],
    "config": {"expression": "* * * * *"},
}]
scheduler = TriggerScheduler(triggers, fire_fn, poll_interval=1)
await scheduler.start()
# ... after ~60s ...
# FIRED: topology=hello source=trigger:every-minute
await scheduler.stop()
```

Disabled triggers (`enabled: false`) are silently skipped. Missing `croniter`
dependency logs a warning and skips cron triggers (other types unaffected).

---

## MCP endpoint

MCP is mounted at `/mcp` using Streamable HTTP transport. Each topology becomes
an MCP tool (`run_{topology_name}`) and resource (`topology://{name}`).

```bash
$ curl -s -X POST http://localhost:8321/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "method": "initialize", ...}'
```

External agents (Claude Code, Cursor, etc.) can connect to the MCP endpoint
and call topology tools directly.

---

## Server configuration (workspace.yaml)

```yaml
server:
  jobs:
    max_concurrent: 5      # default
    timeout_seconds: 300   # default
  mcp:
    enabled: true          # default
```

All server features are opt-in / plug-and-play. A workspace without a `server:`
block uses defaults. Auth is disabled by default (`NoneAuthProvider`).

---

## Full endpoint summary

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | /health | No | Health check |
| GET | /topologies | Yes | List topology names |
| GET | /skills | Yes | List skills with categories |
| GET | /archetypes | Yes | List archetype names |
| GET | /validate | Yes | Validate workspace |
| GET | /triggers | Yes | List configured triggers |
| POST | /run/{topology} | Yes | Submit async job |
| GET | /jobs | Yes | List all jobs |
| GET | /jobs/{id} | Yes | Poll job status |
| GET | /jobs/{id}/stream | Yes | SSE event stream |
| POST | /hooks/{topology} | Yes | Webhook trigger |
| POST | /conversations | Yes | Create conversation |
| GET | /conversations | Yes | List conversations |
| POST | /conversations/{id}/messages | Yes | Send message |
| POST | /mcp | Yes | MCP Streamable HTTP |
