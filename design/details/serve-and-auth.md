---
title: "swarmkit serve — HTTP server, MCP endpoint, and AuthProvider"
description: Production HTTP server with pluggable auth, MCP tool exposure, async jobs, triggers, and streaming. All features opt-in.
tags: [server, mcp, auth, triggers, M10]
status: draft
---

# swarmkit serve — HTTP server, MCP endpoint, and AuthProvider

**Scope:** `packages/runtime/src/swarmkit_runtime/server.py`, new `auth/` module
**Design reference:** §14.1 (execution modes), §18 (MCP integration), §8.5 (governance boundary), §5.4 (triggers)
**Status:** draft

## 1. Overview

One command — `swarmkit serve` — starts a single process that exposes three capabilities:

1. **REST API** — async topology execution, job management, conversations, introspection
2. **MCP endpoint** — Streamable HTTP transport at `/mcp`, exposing topologies as tools to AI IDEs and other MCP clients
3. **Trigger scheduler** — cron, webhook, and file-watch triggers executing topologies on schedule

All three are optional and configured via the `server:` block in `workspace.yaml`. Zero config gives you a local dev server: no auth, all topologies exposed, no triggers. Progressive enablement — add auth, MCP exposure, triggers one line at a time.

The existing `server.py` already has the skeleton: FastAPI app, lifespan, health/topologies/skills/archetypes/run/validate/conversations endpoints. This design extends it with async job execution, auth, MCP, and triggers.

## 2. Server architecture

```
swarmkit serve [workspace-path] [--port 8000] [--host 0.0.0.0]

                 ┌─────────────────────────────────┐
                 │         uvicorn process          │
                 │                                  │
                 │  ┌──────────┐  ┌──────────────┐  │
                 │  │ FastAPI  │  │ MCP endpoint  │  │
                 │  │ REST API │  │ /mcp          │  │
                 │  └────┬─────┘  └──────┬────────┘  │
                 │       │               │           │
                 │       ▼               ▼           │
                 │  ┌────────────────────────────┐   │
                 │  │ AuthProvider middleware     │   │
                 │  └────────────┬───────────────┘   │
                 │               │                   │
                 │               ▼                   │
                 │  ┌────────────────────────────┐   │
                 │  │    WorkspaceRuntime         │   │
                 │  │  (topology execution,       │   │
                 │  │   MCP servers, governance)  │   │
                 │  └────────────────────────────┘   │
                 │                                   │
                 │  ┌────────────────────────────┐   │
                 │  │   Trigger scheduler        │   │
                 │  │  (cron / file_watch tasks)  │   │
                 │  └────────────────────────────┘   │
                 └─────────────────────────────────┘
```

- **Single uvicorn process.** No multi-process workers — the runtime holds stateful MCP server connections and job state. Scale horizontally by running multiple workspace instances behind a load balancer.
- **FastAPI app** handles REST endpoints. Auth middleware intercepts every request before routing.
- **MCP endpoint** at `/mcp` using Streamable HTTP transport (the current MCP spec transport, replacing the deprecated SSE transport).
- **Lifespan manager** starts MCP servers once at boot via `WorkspaceRuntime`, shares them across all requests. Also starts the trigger scheduler and cleans up both on shutdown.
- **Async job execution:** `POST /run` returns a `job_id` immediately. Callers poll via `GET /jobs/{id}` or stream progress via `GET /jobs/{id}/stream` (SSE). Jobs run as asyncio tasks within the single process.

## 3. REST API endpoints

All endpoints return JSON. Error responses use `{"detail": "..."}` with appropriate HTTP status codes.

### Introspection

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| `GET` | `/health` | Liveness check | `{"status": "ok", "workspace": "<id>"}` |
| `GET` | `/topologies` | List topology names | `["advisor", "code-review"]` |
| `GET` | `/skills` | List skills with categories | `[{"id": "search", "category": "capability"}]` |
| `GET` | `/archetypes` | List archetypes | `["researcher", "analyst"]` |
| `GET` | `/validate` | Workspace validation summary | `{"valid": true, "workspace_id": "...", ...}` |

These already exist in `server.py`. No changes needed.

### Async job execution (new)

The current `POST /run/{topology}` is synchronous — it blocks until the topology completes. For production use, callers need async execution with progress streaming.

| Method | Path | Description | Request | Response |
|--------|------|-------------|---------|----------|
| `POST` | `/run/{topology}` | Start async execution | `{"input": "...", "max_steps": 10}` | `{"job_id": "j-abc123", "status": "running"}` |
| `GET` | `/jobs/{id}` | Poll job status | — | `{"job_id": "...", "status": "running\|completed\|failed", "result": {...}\|null}` |
| `GET` | `/jobs/{id}/stream` | SSE stream of progress events | — | Event stream (see below) |

**Job lifecycle:**

```
POST /run/advisor {"input": "What is dharma?"}
← 202 {"job_id": "j-abc123", "status": "running"}

GET /jobs/j-abc123
← 200 {"job_id": "j-abc123", "status": "running", "result": null, "started_at": "..."}

GET /jobs/j-abc123/stream
← 200 text/event-stream
data: {"event": "agent.start", "agent_id": "root", "timestamp": "..."}
data: {"event": "agent.tool_call", "agent_id": "root", "tool": "search", "timestamp": "..."}
data: {"event": "agent.complete", "agent_id": "root", "output": "...", "timestamp": "..."}
data: {"event": "job.complete", "result": {"output": "...", "agent_results": {...}}}

GET /jobs/j-abc123
← 200 {"job_id": "j-abc123", "status": "completed", "result": {"output": "...", "agent_results": {...}}}
```

**SSE event types:**

- `agent.start` — agent begins execution
- `agent.tool_call` — agent invokes a tool/skill
- `agent.delegate` — agent delegates to a sub-agent
- `agent.complete` — agent finishes with output
- `governance.decision` — policy engine evaluates an action
- `job.complete` — topology execution finished
- `job.failed` — topology execution failed with error

**Job storage:** in-memory dict keyed by job_id. Completed jobs are retained for `jobs.retention_hours` (default 24h), then evicted. No persistence across restarts — this is a runtime cache, not a database. Persistent job history belongs in audit events.

**Concurrency:** `jobs.max_concurrent` (default 5) limits parallel topology executions. When the limit is reached, new `POST /run` requests return `429 Too Many Requests`.

### Conversations (existing)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/conversations` | Create conversation for a topology |
| `GET` | `/conversations` | List active conversations |
| `POST` | `/conversations/{id}/messages` | Send message in conversation |

Already implemented. Auth will gate these by `conversations:*` scope.

### Webhook trigger endpoint (new)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/hooks/{topology}` | Receive webhook, fire topology |

Validates webhook signature if the trigger's `auth` block is configured (HMAC, bearer, or API key). Request body is passed as topology input. Returns `202 Accepted` with the job_id of the triggered run.

## 4. MCP endpoint

### Transport

Mounted at `/mcp` on the same FastAPI app. Uses **Streamable HTTP** transport — the current MCP specification transport. SSE transport is deprecated in the MCP spec and not supported.

The MCP endpoint handles the standard MCP protocol: `initialize`, `tools/list`, `tools/call`, `resources/list`, `resources/read`, `prompts/list`, `prompts/get`.

### What gets exposed as MCP tools

Each topology in the workspace becomes an MCP tool:

```json
{
  "name": "run_advisor",
  "description": "Run the advisor topology. Input: a question or task.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "input": {"type": "string", "description": "The question or task"},
      "max_steps": {"type": "integer", "default": 10}
    },
    "required": ["input"]
  }
}
```

Tool calls go through the same async job path as REST `POST /run`. The MCP response waits for completion (MCP tools are synchronous from the client's perspective).

### Optional: MCP server passthrough

Workspace MCP servers can be re-exposed through the SwarmKit MCP endpoint. This lets an AI IDE access workspace tools (search, database, etc.) without configuring each MCP server individually. Controlled by `server.mcp.passthrough_tools`.

### MCP resources

- Workspace description → `swarmkit://workspace` resource
- Each topology description → `swarmkit://topology/{name}` resource

### MCP prompts

Optional pre-built prompt templates, if topologies define them:

- `run_{topology}` — a structured prompt template for executing the topology with guided inputs

### Configuration

```yaml
server:
  mcp:
    enabled: true
    expose_topologies: all       # or ["advisor", "code-review"]
    passthrough_tools: none      # or all, or ["search-server", "db-server"]
```

`expose_topologies: all` is the default when `mcp.enabled: true`. This keeps zero-config simple — enable MCP, get all topologies as tools. Restrict with a list when needed.

`passthrough_tools: none` is the default. Passthrough requires explicit opt-in because it widens the attack surface — workspace MCP servers may have write access to databases, filesystems, etc.

## 5. AuthProvider abstraction

The core abstraction. Mirrors `GovernanceProvider` (§8.5) and `ModelProvider`: narrow interface, built-in implementations, plugin path.

### Interface

```python
class AuthProvider(ABC):
    """Authenticates and authorises incoming requests to the SwarmKit server.

    Every request — REST or MCP — passes through the auth provider before
    reaching any endpoint. Returns an AuthIdentity on success; raises
    AuthError on failure. The identity flows into audit events and is
    available to endpoint handlers.

    This is the server perimeter. It answers "who is this client and what
    endpoints can they hit." It is NOT governance — governance answers
    "can this agent perform this action within the swarm" (see §7 below).
    """

    provider_id: ClassVar[str]

    @abstractmethod
    async def authenticate(self, request: AuthRequest) -> AuthIdentity:
        """Authenticate a request. Raise AuthError if credentials are
        missing or invalid."""
        ...

    @abstractmethod
    async def authorize(
        self, identity: AuthIdentity, resource: str, action: str
    ) -> bool:
        """Check if identity can perform action on resource.

        resource examples:
          "topology:advisor"        — a specific topology
          "topology:*"              — all topologies
          "conversation:abc123"     — a specific conversation
          "mcp:tool:run_advisor"    — an MCP tool
          "jobs:j-abc123"           — a specific job

        action examples:
          "execute"   — run a topology / call an MCP tool
          "read"      — list topologies, poll job status
          "write"     — create conversation, send message
          "admin"     — validate workspace, manage jobs
        """
        ...
```

### Data types

```python
@dataclass(frozen=True)
class AuthRequest:
    """Incoming request context for authentication."""
    headers: dict[str, str]
    path: str
    method: str
    query_params: dict[str, str] = field(default_factory=dict)
    client_ip: str | None = None


@dataclass(frozen=True)
class AuthIdentity:
    """Authenticated client identity. Flows into audit events."""
    client_id: str               # unique identifier
    client_name: str             # human-readable name
    provider: str                # "api_key", "jwt", "oauth2", "none"
    scopes: frozenset[str]       # what the client can access
    metadata: dict[str, Any] = field(default_factory=dict)


class AuthError(Exception):
    """Raised when authentication or authorisation fails."""

    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code
```

### Built-in providers

#### NoneAuthProvider (default)

Always returns a generic identity with wildcard scopes. For local development.

```python
class NoneAuthProvider(AuthProvider):
    """No authentication. Every request is allowed."""

    provider_id: ClassVar[str] = "none"

    async def authenticate(self, request: AuthRequest) -> AuthIdentity:
        return AuthIdentity(
            client_id="anonymous",
            client_name="Anonymous (no auth)",
            provider="none",
            scopes=frozenset({"*"}),
        )

    async def authorize(
        self, identity: AuthIdentity, resource: str, action: str
    ) -> bool:
        return True
```

No headers required. No configuration. This is what you get when you run `swarmkit serve` with no `auth:` block.

#### APIKeyAuthProvider

Reads `Authorization: Bearer <key>` header. Each key maps to a client identity with scopes.

```python
class APIKeyAuthProvider(AuthProvider):
    """API key authentication via Bearer header."""

    provider_id: ClassVar[str] = "api_key"

    def __init__(self, config: dict[str, Any]) -> None:
        self._keys: dict[str, _KeyEntry] = {}
        for entry in config["keys"]:
            resolved_key = self._resolve_ref(entry["key_ref"])
            self._keys[resolved_key] = _KeyEntry(
                client_id=entry["client_id"],
                scopes=frozenset(entry.get("scopes", ["*"])),
            )

    async def authenticate(self, request: AuthRequest) -> AuthIdentity:
        token = _extract_bearer_token(request.headers)
        if not token:
            raise AuthError("Missing Authorization: Bearer header")
        entry = self._keys.get(token)
        if entry is None:
            raise AuthError("Invalid API key")
        return AuthIdentity(
            client_id=entry.client_id,
            client_name=entry.client_id,
            provider="api_key",
            scopes=entry.scopes,
        )

    async def authorize(
        self, identity: AuthIdentity, resource: str, action: str
    ) -> bool:
        required = f"{resource}:{action}"
        return "*" in identity.scopes or required in identity.scopes
```

Configuration:

```yaml
server:
  auth:
    provider: api_key
    config:
      keys:
        - key_ref: env:VEDANTA_API_KEY
          client_id: vedanta-web
          scopes: [topologies:execute, conversations:write, jobs:read]
        - key_ref: env:ADMIN_API_KEY
          client_id: admin
          scopes: ["*"]
```

`key_ref` uses the `env:` prefix to read from environment variables. Future: `vault:`, `file:` prefixes for other secret sources. Keys are never stored in workspace.yaml directly.

#### JWTAuthProvider

Validates JWTs from `Authorization: Bearer <token>`. Supports RS256 and ES256 with JWKS auto-discovery.

```python
class JWTAuthProvider(AuthProvider):
    """JWT authentication with JWKS auto-discovery."""

    provider_id: ClassVar[str] = "jwt"

    def __init__(self, config: dict[str, Any]) -> None:
        self._issuer = config["issuer"]
        self._audience = config["audience"]
        self._jwks_url = config["jwks_url"]
        self._scopes_claim = config.get("scopes_claim", "scope")
        self._jwks_client: PyJWKClient | None = None

    async def authenticate(self, request: AuthRequest) -> AuthIdentity:
        token = _extract_bearer_token(request.headers)
        if not token:
            raise AuthError("Missing Authorization: Bearer header")

        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(self._jwks_url)

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.exceptions.InvalidTokenError as exc:
            raise AuthError(f"Invalid JWT: {exc}") from exc

        scopes_raw = payload.get(self._scopes_claim, "")
        scopes = frozenset(
            scopes_raw.split() if isinstance(scopes_raw, str) else scopes_raw
        )

        return AuthIdentity(
            client_id=payload["sub"],
            client_name=payload.get("name", payload["sub"]),
            provider="jwt",
            scopes=scopes,
            metadata={"iss": payload["iss"], "exp": payload.get("exp")},
        )

    async def authorize(
        self, identity: AuthIdentity, resource: str, action: str
    ) -> bool:
        required = f"{resource}:{action}"
        return "*" in identity.scopes or required in identity.scopes
```

Configuration:

```yaml
server:
  auth:
    provider: jwt
    config:
      issuer: https://auth.example.com
      audience: swarmkit
      jwks_url: https://auth.example.com/.well-known/jwks.json
      scopes_claim: permissions   # JWT claim containing scopes
```

### Writing a custom AuthProvider

The plugin path mirrors `ModelProvider` and `GovernanceProvider`: implement the ABC, register via entry point, reference by name.

**Step 1: Implement the ABC.**

```python
# my_auth_package/oauth2.py

from typing import Any, ClassVar

import httpx

from swarmkit_runtime.auth import AuthError, AuthIdentity, AuthProvider, AuthRequest


class OAuth2AuthProvider(AuthProvider):
    """OAuth2 token introspection provider."""

    provider_id: ClassVar[str] = "oauth2"

    def __init__(self, config: dict[str, Any]) -> None:
        self._introspection_url = config["introspection_url"]
        self._client_id = config["client_id"]
        self._client_secret_ref = config["client_secret_ref"]

    async def authenticate(self, request: AuthRequest) -> AuthIdentity:
        token = _extract_bearer_token(request.headers)
        if not token:
            raise AuthError("Missing bearer token")

        secret = _resolve_ref(self._client_secret_ref)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._introspection_url,
                data={"token": token, "client_id": self._client_id},
                auth=(self._client_id, secret),
            )
            resp.raise_for_status()
            data = resp.json()

        if not data.get("active"):
            raise AuthError("Token is not active")

        return AuthIdentity(
            client_id=data["sub"],
            client_name=data.get("username", data["sub"]),
            provider="oauth2",
            scopes=frozenset(data.get("scope", "").split()),
            metadata={"token_type": data.get("token_type")},
        )

    async def authorize(
        self, identity: AuthIdentity, resource: str, action: str
    ) -> bool:
        required = f"{resource}:{action}"
        return "*" in identity.scopes or required in identity.scopes
```

**Step 2: Register via entry point.**

```toml
# pyproject.toml of the plugin package
[project.entry-points."swarmkit.auth_providers"]
oauth2 = "my_auth_package.oauth2:OAuth2AuthProvider"
```

**Step 3: Reference in workspace.yaml.**

```yaml
server:
  auth:
    provider: oauth2
    config:
      introspection_url: https://auth.example.com/oauth2/introspect
      client_id: swarmkit-server
      client_secret_ref: env:OAUTH2_CLIENT_SECRET
```

**Resolution order:** workspace `class:` override (fully-qualified Python path) > entry-point plugins > built-ins. First match on `provider_id` wins. Same mechanism as `ModelProvider`.

## 6. Auth-audit integration

Every authenticated request generates an audit trail:

```python
@dataclass(frozen=True)
class AuthAuditEvent:
    event_type: str              # "auth.success", "auth.denied", "authz.denied"
    client_id: str               # from AuthIdentity, or "unknown" for failed auth
    provider: str                # auth provider that handled the request
    resource: str                # endpoint / topology / MCP tool
    action: str                  # execute, read, write, admin
    allowed: bool
    timestamp: datetime          # UTC
    client_ip: str | None
    detail: str | None           # reason for denial, if denied
```

Integration points:

- **On successful auth:** `auth.success` event with `client_id`, `provider`, `resource`, `action`.
- **On failed auth** (bad key, expired JWT): `auth.denied` event. `client_id` is `"unknown"`. The denial reason is logged but sensitive details (the invalid key itself) are not.
- **On authorisation failure** (valid identity, insufficient scopes): `authz.denied` event with the `client_id` and the missing scope.
- **Topology runs:** the `AuthIdentity.client_id` is added to the run's audit events as `caller_id`. This connects "who triggered this run" to "what the agents did" — the full causal chain from HTTP request to agent actions.

Events flow through `GovernanceProvider.record_event()` using the existing `AuditEvent` type. The auth layer produces events; it does not consume governance decisions. One-way flow.

## 7. Auth-governance boundary

Auth and governance are separate layers. Conflating them is a design error that would break the separation-of-powers model (§8).

| Concern | Auth (perimeter) | Governance (internal) |
|---------|-------------------|----------------------|
| Question answered | "Who is this client? Can they access this endpoint?" | "Can this agent perform this action with these scopes?" |
| Where it runs | FastAPI middleware, before any endpoint handler | Inside topology execution, around skill invocations |
| Identity type | `AuthIdentity` (client: human, service, API key) | Agent identity (§8.5 `verify_identity`) |
| Scope model | Endpoint-level: `topologies:execute`, `conversations:write` | Agent-level: `repo:read`, `skills:activate` (§16.3) |
| Provider | `AuthProvider` | `GovernanceProvider` |
| Failure mode | HTTP 401/403, request never reaches runtime | Policy denial, agent action blocked, audit logged |

**No crossover:**
- Auth does not grant agent scopes. An authenticated client starts a topology run; the agents within that run are governed by `GovernanceProvider` with their own scope sets defined in topology YAML.
- Governance does not check client identity. An agent's policy evaluation has no knowledge of which API key triggered the run.
- The only connection is `caller_id` in audit events — for traceability, not for policy decisions.

## 8. Trigger execution

Triggers (§5.4, trigger-schema-v1) fire topologies automatically. The server runs them as background tasks.

### Cron triggers

A background asyncio task starts at server boot. It reads all `kind: Trigger` artifacts with `type: cron` from the workspace. Uses `croniter` to compute next-fire times and schedules asyncio timers.

```python
async def _cron_loop(runtime: WorkspaceRuntime, triggers: list[Trigger]) -> None:
    """Background loop that fires cron triggers at scheduled times."""
    while True:
        next_trigger, wait_seconds = _next_due(triggers)
        await asyncio.sleep(wait_seconds)
        await _fire_trigger(runtime, next_trigger)
```

### Webhook triggers

Dedicated endpoint `POST /hooks/{topology}`. When a trigger with `type: webhook` is configured, the server registers a route at the trigger's `config.path` (or defaults to `/hooks/{topology-id}`).

Webhook authentication follows the trigger schema's `auth` block:

- `method: hmac` — validates `X-Hub-Signature-256` (or configurable header) against the webhook body using the referenced secret
- `method: bearer` — validates `Authorization: Bearer <token>` against a configured value
- `method: api_key` — validates a custom header against a configured value

### File-watch triggers

Uses `watchfiles` (async inotify wrapper on Linux, polling fallback elsewhere). Starts a watch task per `type: file_watch` trigger at boot. When a matching event fires, debounces for 500ms (configurable), then triggers the topology.

### Execution path

All triggers create runs through the same `WorkspaceRuntime.run()` path. The trigger source is recorded in the run's audit events:

```python
trigger_source: Literal["cron", "webhook", "file_watch", "manual", "mcp", "rest"]
```

This means trigger-fired runs are indistinguishable from manual runs in terms of governance, execution, and observability. The only difference is the `trigger_source` field in audit, which enables filtering and analytics.

## 9. workspace.yaml configuration

Full `server:` block with all options:

```yaml
server:
  # --- HTTP server ---
  http:
    port: 8000                     # default
    host: 0.0.0.0                  # default; use 127.0.0.1 for local-only
    cors:
      origins: ["*"]              # restrict in production
      allow_methods: ["*"]
      allow_headers: ["*"]

  # --- MCP endpoint ---
  mcp:
    enabled: true                  # default false
    expose_topologies: all         # or ["advisor", "code-review"]
    passthrough_tools: none        # or all, or ["search-server"]

  # --- Authentication ---
  auth:
    provider: none                 # none | api_key | jwt | <plugin-name>
    # config: ...                  # provider-specific (see §5)

  # --- Async jobs ---
  jobs:
    max_concurrent: 5              # max parallel topology executions
    timeout_seconds: 300           # per-job timeout
    retention_hours: 24            # how long completed jobs stay in memory
```

All fields are optional. Omitting the entire `server:` block gives you defaults: port 8000, no auth, no MCP, 5 concurrent jobs.

## 10. Implementation plan

Six PRs, roughly ordered by dependency:

| PR | Scope | Depends on |
|----|-------|------------|
| **PR 1** | Async job execution — `POST /run` returns job_id, `GET /jobs/{id}`, `GET /jobs/{id}/stream` SSE. Refactor existing sync `/run` to use job system internally. | — |
| **PR 2** | `AuthProvider` ABC + `NoneAuthProvider` + `APIKeyAuthProvider`. FastAPI middleware wiring. Auth audit events. | — |
| **PR 3** | MCP endpoint — Streamable HTTP at `/mcp`, topology tools, resource exposure. | PR 1 (uses job execution for tool calls) |
| **PR 4** | `JWTAuthProvider` — JWKS discovery, RS256/ES256, claims mapping. | PR 2 |
| **PR 5** | Trigger execution — cron scheduler + webhook handler + file watcher. All via `WorkspaceRuntime.run()`. | PR 1 |
| **PR 6** | MCP server lifecycle — start once at boot, share across requests, graceful shutdown. MCP passthrough tools. | PR 3 |

Each PR ships tests (unit + integration) and a demo showing the feature working against a real workspace.

## 11. Open questions

- **Should MCP passthrough tools require separate auth from REST?** Probably yes — different trust model. An API key that can execute topologies should not automatically get access to raw database tools passed through from workspace MCP servers. Tentative: MCP passthrough requires `mcp:passthrough:*` scope explicitly.
- **Should the server support HTTPS directly or always sit behind a reverse proxy?** Recommend: always behind a proxy (nginx, Caddy, cloud LB). TLS termination, rate limiting, and request buffering are better handled by dedicated infrastructure. The server listens on plain HTTP. Document this clearly.
- **Rate limiting: built-in or delegated to proxy?** Recommend: proxy for now. The `jobs.max_concurrent` limit handles the most important case (runaway topology execution). Per-client rate limiting is a proxy concern. Revisit if self-hosted deployments without proxies become common.
- **Job persistence across restarts.** Currently in-memory only. For production, should completed job results survive a restart? Tentative: no — audit events are the durable record, job results are ephemeral runtime state. If users need it, a future `persistence` skill can archive results.
- **WebSocket transport for MCP.** The MCP spec also defines a Stdio transport (irrelevant for HTTP servers) and mentions WebSocket as a future option. Streamable HTTP is sufficient for now. Revisit if MCP clients demand WebSocket.

## Test plan

- **Unit — AuthProvider ABC:** `NoneAuthProvider` and `APIKeyAuthProvider` tested against the interface. Mock requests with various header combinations. Scope matching with wildcards.
- **Unit — auth middleware:** FastAPI test client with `NoneAuthProvider` (all requests pass), `APIKeyAuthProvider` (valid key passes, invalid key returns 401, missing scopes return 403).
- **Unit — job manager:** create job, poll status transitions (running -> completed, running -> failed), timeout enforcement, max concurrent limit, retention eviction.
- **Unit — SSE stream:** job progress events appear in correct order in the event stream.
- **Unit — MCP tool generation:** workspace with two topologies produces two MCP tools with correct schemas.
- **Integration — full server:** start server with a test workspace, create conversation, send message, verify auth headers are checked. Requires `MockModelProvider`.
- **Integration — webhook trigger:** POST to `/hooks/{topology}` with correct HMAC signature fires the topology. Invalid signature returns 401.

## Demo plan

- `just demo-serve` — starts server against the reference workspace, shows health check, lists topologies, runs a topology via REST, polls job to completion.
- `just demo-serve-auth` — starts server with API key auth, demonstrates 401 without key, success with key, 403 with insufficient scopes.
- `just demo-serve-mcp` — starts server with MCP enabled, connects an MCP client, lists tools, calls a topology tool.
