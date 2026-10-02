# credential-setup — a conversational-authoring workspace for OAuth clients

A standalone reference workspace that walks the operator through registering an OAuth
client (Google first; GitHub + Notion + Slack follow the same shape) and lands a working
`oauth_clients` row on the runtime. See
[`design/details/google-workspace-setup.md`](../../../design/details/google-workspace-setup.md).

**Not a per-user workspace.** This one runs OFF the runtime's own identity to populate
what every other workspace depends on. DOT, Minder, Hermes-style assistants — all
reach for the same `oauth_clients` row once this swarm has run.

## Layout

```
reference/workspaces/credential-setup/
  workspace.yaml                      # command_packs only (no credentials, no mcp_servers)
  skills/
    register-oauth-client.yaml        # command skill → POST /api/oauth/clients
  archetypes/
    google-workspace-concierge.yaml   # the prompt-driven walker (leader)
    oauth-client-registrar.yaml       # the deterministic last step (worker)
  topologies/
    google-workspace-setup.yaml       # concierge → registrar
  command_packs/
    credential-setup-tools/
      register_oauth_client.py        # stdin JSON → POST /api/oauth/clients
```

## What it does

1. **Concierge** explains the shape, discovers the intended scope list, prints the GCP
   console checklist, receives the uploaded `credentials.json`, scrubs it back to the
   operator for one explicit confirmation, and emits a plan artifact.
2. **Registrar** takes that plan artifact and calls `register-oauth-client`, which
   `POST`s to the runtime's `/api/oauth/clients`. The runtime stores it issuer-keyed
   (#1004), so one row serves every Google API.
3. The topology returns the stored display row (no secret).

## How to run it

```bash
# Terminal 1 — runtime with the setup workspace loaded.
uv run swarmkit serve --workspace reference/workspaces/credential-setup --port 8000

# Terminal 2 — kick off the topology.
uv run swarmkit run reference/workspaces/credential-setup google-workspace-setup
```

A CLI shortcut (one-word invocation that defers to `swarmkit run` under the hood) + the
Connections-page "Needs setup" button are tracked in a follow-up. The `/connections`
surface in `packages/ui/` and `reference/apps/dot/` will auto-launch this topology when a
credential has no `oauth_clients` row for its issuer.

## Reusable for other providers

The `oauth-client-registrar` archetype + the `register-oauth-client` skill + the command
pack script are provider-agnostic — they take any `{issuer, client_id, client_secret,
client_type, display_name, scopes}` plan.

To add a new provider (e.g. GitHub Apps), ship a new concierge archetype with a
provider-specific prompt + checklist, and a new topology that pairs it with the shared
registrar. The runtime API surface (`/api/oauth/clients`, `/api/oauth/login`,
`/auth/mcp/probe`) stays unchanged.
