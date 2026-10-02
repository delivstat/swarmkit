# google-workspace-setup — a conversational-authoring swarm for Google OAuth setup

**Status:** draft, part 2 of [[hermes-oauth-lessons]] (issue #1000).
**Depends on:** [[oauth-persistent-clients]] (now issuer-keyed per #1003/#1004).
**Blocks:** [[dot-app]] real-runtime wiring; the pattern repeats for GitHub, Notion, etc.

## Goal

Replace a markdown docs page ("open Google Cloud Console, click here, then here, then
upload the JSON") with an **authored swarm** that walks the operator through the Google
Cloud setup — matching the Hermes pattern (*"Help me set up the google-workspace
skill"*). Produces a working `oauth_clients` entry for `https://accounts.google.com` at the
end, without the operator ever leaving the SwarmKit portal (or the DOT companion app).

Setup becomes a swarm artifact that lives in a reference workspace, not static docs.
Reusable: same scaffold ships for GitHub, Notion, Slack, etc.

## Non-goals

- Automating Google Cloud project creation. GCP has no stable programmatic path for a brand-
  new project with OAuth consent screen configured end-to-end from a non-authenticated client,
  and even if it did we'd need the operator's Google credentials. The swarm orchestrates the
  **operator's clicks**; it does not click for them.
- Multi-tenant OAuth client management. Still single-registration-per-issuer per
  [[oauth-persistent-clients]].
- A UI framework for setup flows. The swarm renders through the normal item-handling surface
  (buttons + chat + result panel from [[dot-app]] §3.3) and later the portal's generic
  review surface — no bespoke UI.
- Setup for providers that *do* support DCR. Those don't need this swarm; registration
  happens automatically. The swarm answers "please help me register with Google / GitHub /
  Notion" where DCR isn't an option.

## The shape

A reference workspace at `reference/workspaces/credential-setup/` containing:

```
reference/workspaces/credential-setup/
  workspace.yaml                      # declares the archetypes + topologies
  archetypes/
    google-workspace-concierge.yaml   # the step-by-step walker
    oauth-client-registrar.yaml       # idempotent: parses credentials.json, calls POST /api/oauth/clients
  topologies/
    google-workspace-setup.yaml       # the user-facing entry point
```

A single topology, `google-workspace-setup`, with two archetypes:

### Archetype: `google-workspace-concierge` (leader)

Role: the step-by-step walker. Prompt-driven, no custom code. Reads from the operator and
writes a structured plan the registrar can act on.

Steps the concierge takes the operator through, each as a human-visible checkpoint:

1. **Explain the shape.** One short message: you'll need a GCP project, you'll create one
   OAuth 2.0 client (Desktop app type), you'll upload the JSON. The scopes come from the
   workspaces that will use the credentials (Gmail readonly, Calendar readonly, ...).
2. **Discover intended scopes.** Reads `credentials:` and `mcp_servers:` from the active
   workspace to assemble the Google scope list. Shows the operator the exact scopes it will
   request and asks for confirmation. ("You're about to request `gmail.readonly` and
   `calendar.readonly`. Add anything?")
3. **Walk the GCP steps.** Posts a short checklist pointing at
   `https://console.cloud.google.com`:
   - Create a project (or pick an existing one).
   - Enable the Gmail API and Calendar API.
   - Configure the OAuth consent screen (External, add yourself as a test user, paste the
     scope list the concierge printed).
   - Create an OAuth 2.0 Client → **Desktop app** → download the JSON.
4. **Receive the credentials.** Operator uploads `credentials.json` via a file-input in the
   DOT companion app or the portal. The concierge echoes back a scrubbed version (first and
   last 4 chars of client_id, no secret) and asks "is this right?" for one explicit
   confirmation.
5. **Hand off to the registrar.** Emits a structured plan artifact:
   ```json
   {
     "action": "register_oauth_client",
     "client_id": "...",
     "client_secret": "...",
     "client_type": "desktop",
     "scopes": [...]
   }
   ```

### Archetype: `oauth-client-registrar` (worker)

Role: the deterministic bit. Takes the plan artifact and:

1. Discovers the issuer from one of the workspace's MCP endpoints (e.g.
   `https://gmailmcp.googleapis.com/mcp/v1` → `https://accounts.google.com`) via the
   runtime's `/auth/mcp/probe`.
2. Calls `POST /api/oauth/clients` with the plan's fields + the derived issuer.
3. Returns the created display row (no secret) as its result artifact.
4. Immediately kicks off a first login attempt (through `POST /api/oauth/login`) so the
   operator sees the "it worked" state on the Connections page without a round-trip through
   the setup swarm.

This archetype is a thin wrapper — one `tool_use` skill backed by the runtime's own HTTP
API. Keeping it as an archetype rather than a skill means it audits + visualizes the same
as any other topology step, and errors (bad JSON, missing scopes, Google rejecting the
scope list) surface in the normal run inspector.

## Discovery — "I need Google access, help me set it up"

Three ways the operator triggers this:

1. **From the Connections page (`/connections` in DOT, or portal equivalent):** when a
   credential's issuer has no `oauth_clients` row, the status badge reads "Needs setup" and
   the action button says "Set up Google" (or whichever provider). Clicking launches
   `google-workspace-setup` with the credential's endpoint as input.
2. **From the agent chat surface:** the operator types *"Help me set up Google"*. The agent
   recognizes the intent (via the standard `skill-authoring` interpreter) and invokes the
   setup topology.
3. **From the CLI:** `swarmkit setup google` runs the topology in a terminal, with the file
   upload prompt replaced by a filesystem path argument.

All three paths run the same topology — surface is incidental.

## What makes this reusable (the GitHub/Notion/Slack pattern)

The `oauth-client-registrar` archetype is provider-agnostic — it just needs an issuer URL
and a credential JSON. The provider-specific knowledge lives in the concierge archetype's
prompt + checklist.

Future setup swarms:

- `github-app-setup` — different checklist (GitHub App vs OAuth App choice, permissions),
  same registrar.
- `notion-integration-setup` — different checklist (internal integration vs public),
  same registrar.

Each new provider ships as a reference-workspace variant. The runtime API surface
(`/api/oauth/clients`, `/api/oauth/login`, `/auth/mcp/probe`) stays unchanged.

## Where the setup entry lives in the workspace

The setup topology is a **standalone reference workspace**, not a modification to
`reference/workspaces/dot/`. Reasons:

- Setup is cross-cutting — the same `google-workspace-setup` serves DOT, a future Minder
  Google-integration workspace, any Hermes-style assistant. Living in `dot/` would bind it
  to DOT.
- Setup must run even when the owning workspace is misconfigured (that's the whole point).
  Keeping it in a sibling workspace side-steps "chicken and egg" problems where the
  workspace can't load because its credentials aren't registered.

The DOT companion app links to this setup workspace by name when it detects a missing
oauth_client row.

## Test plan

Unit:
- `oauth-client-registrar` archetype: given a valid plan artifact, produces the right
  `POST /api/oauth/clients` call. Given an unknown issuer (probe fails), returns a
  structured error the concierge can show.

Integration:
- The full topology against a mock OAuth provider: concierge collects a fake
  `credentials.json`, registrar calls through the runtime, resulting `oauth_clients` row
  exists.
- Repeat registration is idempotent (replaces the row, doesn't error).

E2E:
- `swarmkit setup google` in a test workspace produces a working oauth_clients row.
- A follow-up `swarmkit run morning-brief` succeeds (end-to-end from "I have no credentials"
  to "I have a working brief").

## Demo plan

A `just demo-google-setup` target:

1. Spins up a mock OAuth server (reused from the #1002 test rig).
2. Runs the `google-workspace-setup` topology with the mock JSON piped in.
3. Shows the resulting `GET /api/oauth/clients` row and the resulting
   `POST /api/oauth/login` auth URL.

Transcript suitable for the PR body.

## Open questions

- **Q1 — File upload surface.** DOT companion app needs a file-input + POST endpoint to
  shuttle the JSON to the runtime. Reuse the existing artifact-upload surface or new
  endpoint? *Proposed default:* dedicated `POST /api/setup/credentials.json` on the runtime
  that the setup topology reads at a specific waypoint. Keeps the generic artifact store
  out of the OAuth plaintext path.
- **Q2 — Who owns the operator's confirmation gate?** The concierge needs an "is this
  right?" HITL step after showing the scrubbed credentials. Use the standard approval-gate
  mechanism (per [[approvals-queue]]) with a specific `scopes:[approvals:resolve]` ask.
  *Proposed default:* yes, exactly that — avoid inventing a new HITL path.
- **Q3 — Where does the test-user list live?** For personal use, the operator adds
  themselves as a Google test-user; the concierge should know their email. Read from
  `${OWNER_EMAIL}` env, from workspace-config, or ask inline?
  *Proposed default:* inline question with the OWNER_EMAIL env as the pre-filled default.
- **Q4 — "Published" OAuth client option.** For a hosted SwarmKit SaaS install (future),
  SwarmKit the project would publish a verified Google OAuth client that every install
  reuses — zero setup. Out of scope here but noted so the data model doesn't preclude it
  later.
