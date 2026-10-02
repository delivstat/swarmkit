# skill-requires-credentials — the explicit skill ↔ credential link

**Status:** draft, part 3 of [[hermes-oauth-lessons]] (issue #1000).
**Depends on:** [[oauth-persistent-clients]], [[google-workspace-setup]].
**Related:** [[skill-schema-v1]], [[per-caller-credential-delegation]].

## Goal

Make the "which credential does this skill need" relationship explicit, instead of implicit
through MCP server naming. Three concrete UX improvements fall out trivially:

1. The Connections page can show "Used by *N* skills" per credential, expandable to the list.
2. The Connections page warns before disconnect: "this will break Gmail-search, Gmail-draft, …".
3. Skill activation blocks (or auto-launches [[google-workspace-setup]]) when a required
   credential has no `oauth_tokens` row for the caller.

Today, "skill `gmail.search_threads` needs credential `gmail`" is derived at runtime from the
tool name's server prefix. Nothing in the manifest says it; nothing in the portal shows it;
disconnecting Gmail silently breaks six skills.

## Non-goals

- Changing how credentials are declared (`workspace.yaml:credentials`) or bound to MCP servers
  (`mcp_servers[].credentials_ref`). Those stay unchanged — this is additive.
- Automatic credential binding at skill-activation time. A skill *declares* what it needs;
  the operator still binds credentials to servers via workspace YAML.
- Multi-credential-per-skill resolution tie-breakers. A skill lists the credentials it
  requires; the runtime checks all of them are present. No voting / fallback semantics.

## Schema change

Add one optional field to the skill manifest (`packages/schema/schemas/skill.schema.json`):

```json
{
  "properties": {
    "requires_credentials": {
      "type": "array",
      "items": { "$ref": "#/$defs/identifier" },
      "uniqueItems": true,
      "description": "credential_ids this skill needs resolved. Each must exist in the workspace's `credentials:` block at activation time; each must have an oauth_tokens row for the caller before the skill can execute."
    }
  }
}
```

Pure addition. Existing skills (every one in `swarmkit-skills` catalogue + every inline
workspace skill) stays valid; the field is optional. Skills that don't declare it fall back
to today's implicit server-prefix inference, so there's no breakage.

The Python + TypeScript schema validators regenerate from the same JSON schema (per the
canonical-schema discipline in [[schema-change-discipline]]).

## Runtime wiring

At workspace load time, the skill registry builds a reverse index:

```
credential_id → set[skill_id]
```

Two sources feed the index:

- **Explicit:** `skill.requires_credentials: [credential_id, ...]`. Authoritative.
- **Implicit fallback:** for `mcp_tool`-backed skills without a `requires_credentials` field,
  the registry derives one from the server's `credentials_ref` — preserving today's
  behaviour for every catalogue skill that hasn't been updated yet. Logged at DEBUG so the
  operator (and the catalogue maintainers) can see which skills are riding the fallback.

The index is accessible via a new runtime method on the skill registry:
`skills_requiring(credential_id) -> list[Skill]`.

## HTTP surface

### `/api/oauth/my-credentials` — add a `used_by` field per row

Non-breaking additive change. Each row grows a `used_by: [{id, name}]` list of skills that
require it. Only `id` + `name` to keep the payload bounded; the UI links to the skills page
for the full manifest.

```json
{
  "owner": "owner@example.com",
  "credentials": [
    {
      "credential_id": "gmail",
      "source": "oauth",
      "identity": "per-user",
      "endpoint": "https://gmailmcp.googleapis.com/mcp/v1",
      "connected": true,
      "scopes": ["gmail.readonly"],
      "expires_at": "...",
      "expired": false,
      "used_by": [
        {"id": "gmail-search-threads", "name": "Gmail: search threads"},
        {"id": "gmail-draft-reply",    "name": "Gmail: draft reply"}
      ]
    }
  ]
}
```

### `/api/skills/{skill_id}` — surfaces `requires_credentials` on the detail

Already returns the manifest; the field appears unchanged.

### Activation refusal

`POST /api/skills/{skill_id}/activate` grows a precondition check: if any
`requires_credentials` entry has no row in `oauth_tokens` for the owner, respond 409
("missing_credentials") with the list. The portal catches the 409 and auto-launches the
matching setup swarm — `google-workspace-setup` for a Google-shaped missing credential,
etc. — resolved via a `(issuer → setup_topology)` map maintained alongside the OAuth client
registry.

The 409 shape:

```json
{
  "error": "missing_credentials",
  "missing": [
    {
      "credential_id": "gmail",
      "issuer": "https://accounts.google.com",
      "setup_topology": "google-workspace-setup"
    }
  ]
}
```

## UI changes

### Connections page

- New right-side column `Used by`: "`N` skills" chip. Clicking expands the list inline.
- Disconnect button: before firing the DELETE, read `used_by.length`. If > 0, confirm with
  a dialog that names the top three skills ("will disable Gmail search, Gmail draft, Gmail
  archive and 3 others"). The dialog is one `window.confirm` for the first iteration — no
  bespoke modal.
- "Needs setup" state: shown when `oauth_clients` has no row for the credential's issuer.
  Links to the setup topology instead of the regular Connect button.

### Skills page

- Each skill row that declares `requires_credentials` shows a status badge:
  - **Ready** (green) — all required credentials are connected.
  - **Needs connection** (amber) — a required credential has an `oauth_clients` row but no
    user token; one-click Connect inline.
  - **Needs setup** (red) — a required credential has no `oauth_clients` row at all; one-
    click to the setup swarm.

Both pages derive their state from the two shipped surfaces; no new store, no new fetch.

## Catalogue migration

The `swarmkit-skills` catalogue repo gets a one-time pass:

- Each `mcp_tool` skill with a stable, single-credential server (Gmail, Calendar, GitHub,
  Notion, Linear, HubSpot, Slack, …) adds `requires_credentials: [<server-id>]` to its
  manifest. Mechanical change; one PR per bundle in the catalogue.
- Skills with no credentials (pure LLM prompts, deterministic validators, local tools) stay
  untouched — the field is optional.
- CI check in the catalogue repo: `mcp_tool` skills whose server has `credentials_ref` must
  carry `requires_credentials`. Catches drift at manifest publish time.

The fallback path (implicit from server prefix) stays supported indefinitely so a workspace
pinning an old catalogue version keeps working. The migration deprecates the fallback with
a WARN log but doesn't remove it.

## Test plan

Schema:
- `requires_credentials` validates as an array of identifiers; duplicates rejected; missing
  field accepted; unknown credential_id values accepted at the schema layer (resolved at
  runtime).
- Round-trip through the Python + TS validators.

Runtime:
- Skill registry index: a skill with explicit `requires_credentials` indexes correctly.
- Fallback index: an mcp_tool skill without the field derives from server prefix; a non-
  mcp_tool skill without the field produces an empty requirement list.
- `skills_requiring(credential_id)` returns the expected skills in a fixture workspace.

HTTP:
- `GET /api/oauth/my-credentials` carries `used_by` with the right skills.
- `POST /api/skills/{skill_id}/activate` returns 409 missing_credentials when a required
  credential has no token; 200 when the token exists; 409 names the setup topology.

E2E:
- `swarmkit setup google` → `swarmkit skills activate gmail-search-threads` → runs green.
- Running the same skill before setup → 409 with pointer; running the setup swarm clears it.

## Demo plan

`just demo-skill-requires-credentials`:

1. Loads a fixture workspace with `gmail` declared, no `oauth_clients` or `oauth_tokens`.
2. Activates `gmail-search-threads` → prints the 409 and the pointer to `google-workspace-setup`.
3. Runs the setup topology against the mock OAuth server (reused from [[google-workspace-setup]]).
4. Re-activates the skill → prints the ready state.
5. Shows `GET /api/oauth/my-credentials` with the `used_by` field populated.

PR-body-length transcript.

## Open questions

- **Q1 — Scope the fallback deprecation.** Keep the server-prefix fallback forever, or
  remove it in a future major (v2 of the skill schema)? *Proposed default:* keep forever —
  the fallback is harmless, and some skills legitimately exist outside the catalogue model.
- **Q2 — Multiple credentials per skill.** `requires_credentials: [gmail, drive]` for a
  skill that reads a Gmail attachment then stores to Drive. All-or-nothing today — any
  missing credential blocks. Should a skill be able to run with partial credentials and
  degrade gracefully? *Proposed default:* all-or-nothing (strict). Graceful degradation is
  per-skill logic that lives in prompts, not the runtime.
- **Q3 — Which page owns the "setup Google" button when multiple skills share a missing
  credential?** Connections page (per credential) and Skills page (per skill) could both
  show the button. *Proposed default:* both. They route to the same setup topology; which
  surface the operator starts from doesn't matter.
- **Q4 — `requires_credentials` on archetypes?** An archetype declares its skills; those
  skills carry the requirement. Hoisting it to the archetype would be redundant. *Proposed
  default:* no — skill-level only.
