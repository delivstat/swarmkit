# HTTP API

Every endpoint `swarmkit serve` exposes, generated from the server's own OpenAPI document (`GET /openapi.json` on a running instance has the schemas; `/docs` renders them). 94 operations. The prose reference — auth modes, triggers, attachments, SSE — is [Serve mode](serve.md); the event contract an application consumes is [Events](events.md).

Paths are relative to the server root. `{...}` segments are path parameters.

## Runs and jobs

| Method | Path | What it does |
|---|---|---|
| `POST` | `/hooks/{topology_name}` | Fire a webhook: an HMAC-signed request starts the named topology, or ingresses a pipeline |
| `GET` | `/jobs` | Jobs currently known to this process (in-memory); `/jobs/history` is the durable list. |
| `GET` | `/jobs/history` | Every recorded run, newest first — or just one pipeline run's stages when |
| `GET` | `/jobs/{job_id}` | One job, from the in-memory store or — failing that — the durable one. |
| `GET` | `/jobs/{job_id}/diff` | The unified diff a harness run produced, per agent. |
| `POST` | `/jobs/{job_id}/resume` | Continue a run that parked on a human gate. |
| `POST` | `/jobs/{job_id}/stop` | Ask a running job to stop at its next agent boundary. |
| `GET` | `/jobs/{job_id}/stream` | A job's events as they happen, as server-sent events. |
| `POST` | `/run/{topology_name}` | Submit a run of a topology; returns a job id to poll, stream or resume. |

## Events

| Method | Path | What it does |
|---|---|---|
| `GET` | `/events` | Events in log order from a position. |
| `POST` | `/events/signal` | Ingress a pipeline event by correlation id — the application telling a run what happened |

## Review and gates

| Method | Path | What it does |
|---|---|---|
| `GET` | `/gates/{gate_id}` | A gate's state with its approval policy applied. |
| `GET` | `/review` | Pending items, optionally narrowed to one ``kind`` and/or one gate. |
| `GET` | `/review/all` | Every review item, pending or not, optionally narrowed to one kind and/or one gate. |
| `GET` | `/review/{item_id}` | One review item. |
| `POST` | `/review/{item_id}/answer` | Answer a question a run asked; a bare integer selects one of its options. |
| `POST` | `/review/{item_id}/approve` | Approve a pending review item as the authenticated caller. |
| `POST` | `/review/{item_id}/reject` | Reject a pending review item as the authenticated caller. |
| `POST` | `/review/{item_id}/resolve` | Resolve a multi-party approval role-task as the authenticated caller. |

## Artifacts

| Method | Path | What it does |
|---|---|---|
| `GET` | `/artifacts` | Every artifact reference recorded under one correlation id. |
| `GET` | `/artifacts/{ref}` | One artifact's content, by its `<correlation>/<stage-or-run>/<name>` reference. |

## Conversations

| Method | Path | What it does |
|---|---|---|
| `GET` | `/conversations` | Every conversation on this instance, newest first. |
| `POST` | `/conversations` | Start a conversation with a topology; returns its id. |
| `GET` | `/conversations/{conversation_id}` | One conversation's full message history. |
| `POST` | `/conversations/{conversation_id}/messages` | Send a message into a conversation; the reply streams back as server-sent events. |

## Governed memory

| Method | Path | What it does |
|---|---|---|
| `GET` | `/memory` | Search governed memory by text, optionally narrowed to one type. |
| `POST` | `/memory` | Write a fact through the same governed path an agent writes through. |
| `GET` | `/memory/item` | One memory item by id, with its history. |
| `GET` | `/memory/quarantine` | Memory writes held for a human because they contradict what is stored. |
| `POST` | `/memory/quarantine/{quarantine_id}/resolve` | Resolve a quarantined memory write: accept it, reject it, or keep both. |

## Workspace and introspection

| Method | Path | What it does |
|---|---|---|
| `GET` | `/.well-known/agent-card.json` | The instance's Agent Card — public, one skill per topology. |
| `POST` | `/a2a` | The JSON-RPC endpoint for every topology; the message names its skill. |
| `POST` | `/a2a/{topology}` | The per-topology JSON-RPC endpoint — the card's per-skill `url`. |
| `GET` | `/a2a/{topology}/card` | The per-topology card, for a client that should see one skill only. |
| `GET` | `/archetypes` | The archetypes in this workspace, by id. |
| `GET` | `/audit` | Append-only audit events, newest-first (read-only; the media pillar exposes no |
| `GET` | `/capabilities` | What this instance can do — the control plane reads this at enroll/refresh. |
| `GET` | `/comprehension` | Comprehension-debt signals from the audit log — same data as `swarmkit comprehension`. |
| `GET` | `/contracts` | The contracts in this workspace, by id. |
| `GET` | `/funnels` | The funnels in this workspace, by id. |
| `GET` | `/health` | Liveness: the instance is up and its workspace loaded. |
| `GET` | `/observability/runs/{run_id}/trace` | The finished run's span tree (topology.run → agent.step → tool.call) for a UI waterfall, |
| `GET` | `/skills` | The skills in this workspace, by id. |
| `GET` | `/storage` | Where this instance's data actually lives — one entry per store. |
| `GET` | `/system` | Everything the System page needs: versions, storage resolution, environment. |
| `GET` | `/topologies` | The topologies in this workspace, by id. |
| `GET` | `/triggers` | The triggers configured on this instance (cron, webhook, pipeline events). |
| `GET` | `/usage` | Token usage and cost across every run on this instance. |
| `GET` | `/usage/{job_id}` | Token usage and cost for one job, per agent. |
| `GET` | `/validate` | Validate every artifact in the workspace and report what is wrong. |
| `GET` | `/workspace/reachability` | Declared configuration that no code path reaches. |
| `GET` | `/workspace/verification` | How strongly each agent's output is checked. |

## Canary deployments

| Method | Path | What it does |
|---|---|---|
| `GET` | `/canary` | The canary routes on this instance and their metrics. |
| `POST` | `/canary/{topology_name}` | Start a canary at runtime (design 26 Layer B): split traffic to a newly-deployed version. |
| `POST` | `/canary/{topology_name}/promote` | Make the canary version the default for a topology. |
| `POST` | `/canary/{topology_name}/rollback` | Withdraw a topology's canary and route everything to the stable version. |

## Authentication

| Method | Path | What it does |
|---|---|---|
| `GET` | `/auth-info` | Unauthenticated: advertise the server's auth mode (+ OIDC issuer/audience for jwt) so a |
| `GET` | `/auth/mcp/callback` | Where the provider sends the person back. |
| `GET` | `/auth/mcp/probe` | Does this server speak OAuth, and where? Step 2 of the portal flow. |
| `GET` | `/whoami` | The *authenticated* caller's identity — as opposed to ``/auth-info``, which is public and |

## Fleet

| Method | Path | What it does |
|---|---|---|
| `POST` | `/fleet/enroll-token` | Mint a one-time fleet enrollment token for a scope (serve:admin). |
| `DELETE` | `/fleet/identity/{fleet_id}` | Forget a fleet's pinned public key (serve:admin) so it may deliberately re-key on the |
| `DELETE` | `/fleet/membership/{membership_id}` | Eject a fleet — revoke its membership; its key stops authenticating (serve:admin). |
| `GET` | `/fleet/memberships` | The fleets registered with this instance (serve:admin — owner-only). No secrets; adds |
| `POST` | `/fleet/refresh` | Rotate the caller's membership key. Authenticates with the *current* key (Bearer); the |
| `POST` | `/fleet/register` | Enroll a fleet with a Bearer enrollment token and its signed identity; the token is |
| `GET` | `/fleet/state` | Full observed state — every artifact's *content* (not just names like /capabilities). |
| `POST` | `/fleet/state/artifacts` | Fetch the *content* of specific artifacts (the body-fetch half of delta sync). The body |
| `GET` | `/fleet/state/manifest` | The names-only manifest of the observed state — every artifact's id/version/content_hash, |

## Portal API (`/api/*` — what the web portal calls)

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/a2a/agents` | The remote agents this workspace can call — every `agent` skill with a `card_url`. |
| `GET` | `/api/a2a/probe` | Fetch a remote Agent Card so a person can pick a skill *before* a skill file exists. |
| `GET` | `/api/archetypes/{archetype_id}` | One archetype, resolved. |
| `PUT` | `/api/archetypes/{archetype_id}` | Replace an archetype's YAML; validated before it is written. |
| `GET` | `/api/archetypes/{archetype_id}/yaml` | An archetype's YAML as written on disk. |
| `PUT` | `/api/contracts/{contract_id}` | Replace a contract's YAML; validated before it is written. |
| `GET` | `/api/contracts/{contract_id}/yaml` | A contract's YAML as written on disk. |
| `PUT` | `/api/funnels/{funnel_id}` | Replace a funnel's YAML; validated before it is written. |
| `GET` | `/api/funnels/{funnel_id}/yaml` | A funnel's YAML as written on disk. |
| `GET` | `/api/oauth/credentials` | Stored tokens, as metadata. Never bytes. |
| `DELETE` | `/api/oauth/credentials/{credential_id}` | Forget a token, and revoke it upstream where the provider supports revocation. |
| `POST` | `/api/oauth/login` | Begin a login. Returns the URL the portal should open in a popup. |
| `POST` | `/api/reload` | Re-read the workspace from disk and return its validation report. |
| `GET` | `/api/schema/{artifact_type}` | The canonical JSON Schema for an artifact type — drives the UI's schema-generated |
| `GET` | `/api/skills/{skill_id}` | One skill, resolved. |
| `PUT` | `/api/skills/{skill_id}` | Replace a skill's YAML; validated before it is written. |
| `GET` | `/api/skills/{skill_id}/yaml` | A skill's YAML as written on disk. |
| `POST` | `/api/topologies` | Create a topology from YAML; validated against the schema before it is written. |
| `GET` | `/api/topologies/{topology_id}` | One topology, resolved: agents, archetypes and skills expanded. |
| `PUT` | `/api/topologies/{topology_id}` | Replace a topology's YAML; validated against the schema before it is written. |
| `DELETE` | `/api/topologies/{topology_id}` | Delete a topology file from the workspace. |
| `GET` | `/api/topologies/{topology_id}/yaml` | A topology's YAML as written on disk. |
| `GET` | `/api/workspace/config` | The editable infrastructure sections of workspace.yaml — credentials and MCP servers — |
| `PUT` | `/api/workspace/config/{section}/{entry_id}` | Create or replace one entry in a workspace.yaml section (`credentials` or `mcp_servers`); |
| `DELETE` | `/api/workspace/config/{section}/{entry_id}` | Remove one entry from a workspace.yaml section; the workspace reloads if the file |
