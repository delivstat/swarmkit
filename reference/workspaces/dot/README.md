# DOT — the Daily Optimization Tracker reference workspace

The reference workspace for [DOT](../../design/details/dot-workspace.md). Aggregates Gmail +
Google Calendar into a ranked morning brief; delegates per-item work behind HITL gates.

**Read-only Sprint 1** — no sending, no scheduling. Send/schedule with approval lands in
Sprint 6+ per the design note.

## Layout

```
reference/workspaces/dot/
  workspace.yaml               # credentials, mcp_servers, provider settings
  archetypes/
    context-aggregator.yaml    # pulls Gmail + Calendar into a raw bundle
    item-ranker.yaml           # ranks ≤10 actionable items with output_schema
    email-drafter.yaml         # drafts a reply — never sends
    meeting-prepper.yaml       # prep note for an upcoming meeting
    conversation-retriever.yaml # finds and summarises a past conversation
  topologies/
    morning-brief.yaml         # aggregate → rank → present
    handle-item.yaml           # per-item dispatcher, invoked when human picks
```

## What runs first

Before the runtime can execute anything against real Gmail/Calendar, the owner must connect
both accounts:

1. `swarmkit serve --workspace reference/workspaces/dot` on the runtime host.
2. Open the portal, navigate to **Connections**.
3. Click **Connect Gmail** — Google's OAuth flow runs, the runtime stores the encrypted
   token owner-scoped.
4. Click **Connect Google Calendar** — same flow, different endpoint.
5. Set `DOT_OWNER` in the environment to the identity that just connected (the same one
   the portal signed in as).

The workspace's two `credentials` entries interpolate `${DOT_OWNER}` — a missing value fails
loudly at startup rather than silently defaulting to something.

## Running the morning brief

Ad-hoc for Sprint 1 (until the companion app under `reference/apps/dot/` lands — see PR 5 in
the [design note's split](../../../design/details/dot-workspace.md#split-for-reviewability)):

```bash
swarmkit run morning-brief --workspace reference/workspaces/dot
```

The output is a JSON blob matching `item-ranker`'s `output_schema`:

```json
{
  "items": [
    {
      "id": "...",
      "source_type": "email",
      "source_id": "<thread id>",
      "title": "Reply to Alice on Q3 planning",
      "why": "Alice asked a direct question; response window is today",
      "suggested_action": "draft-reply"
    }
  ]
}
```

That JSON is what the DOT app renders as a card stack, and what a scheduled trigger would
commit to the observation log.

## Running a per-item action

Given an item from the brief, invoke `handle-item` with the item's fields:

```bash
swarmkit run handle-item --workspace reference/workspaces/dot --input '{
  "item_id": "...",
  "source_type": "email",
  "source_id": "<thread id>",
  "suggested_action": "draft-reply",
  "user_intent": "push back on the timeline"
}'
```

`user_intent` is optional — the scoped-chat surface's payload when the human types instead
of clicks a button. Every per-item run is read-only in Sprint 1; the returned draft or prep
note is text, never an outbound side effect.

## What's not in this workspace yet

Per the [design note](../../design/details/dot-workspace.md) sprint plan:

- **Sprint 2** — Sandbox lifecycle for authored artefacts (`status: draft`, sandbox-run mode,
  Drafts drawer in the app, promote/refine/discard/rollback affordances). Ships BEFORE any
  authoring feature.
- **Sprint 3** — Layer 1 self-authoring: DOT invokes `skill-authoring` to produce draft
  archetypes/topologies for its own workspace.
- **Sprint 4** — DOT plugin manifest schema + registry + hot-reload.
- **Sprint 5** — `dot-authoring` topology; recursive loop closes.
- **Sprint 6+** — send-with-approval and schedule-with-approval, wiring the `outbound-approval`
  gate scaffolded in `handle-item.yaml` from `noop` to `enforce`.

## Establishing the "reference workspace" pattern

DOT is the first reference *workspace* (as opposed to reference topologies at
`reference/topologies/`). A workspace is a topology plus its archetypes plus its credentials
plus its MCP wiring — a self-contained, runnable artefact rather than a snippet users have
to lift into their own workspace. The tree earns a `workspaces/` sibling to `topologies/` for
the same reason `apps/` will earn one in PR 5: an artefact that runs end-to-end deserves a
place that says so.
