---
title: Spaces pattern — composing a shared-surface UX from existing primitives
description: How to build a 'Spaces'-like durable, auditable, shared workspace surface in a SwarmKit app without adding runtime routes, schema, or storage. Five primitives, one worked example.
tags: [patterns, app-layer, docs]
status: proposed
tracking: "#1017"
---

## What a Space is

A **Space** is a persistent, shared, scoped surface that holds the state of
an ongoing collaboration between agents and humans. Think of a Notion page
dedicated to one investigation, a Linear sub-project, or a chat thread with
a durable canvas on the side. The specific shape varies — Notion-like,
Linear-like, chat-thread-like — but the primitives underneath are the same:

- a **stable id** that survives across process restarts and multiple agent runs,
- an **append-only log** of what happened inside it,
- an **approval seam** so a human can review and gate specific transitions,
- a **surface agents can read** without replaying the whole history, and
- a **lifecycle** so stale Spaces don't accumulate forever.

OpenDots (the SwarmKit-adjacent reference project) ships a `Space` primitive
in its app layer. This note documents how to compose the same user-facing
behaviour out of primitives SwarmKit already ships, **without any runtime
addition**. Keeping it in the app layer means different apps can model Spaces
differently and the runtime does not grow a specialised surface it does not
need.

## Non-goals

- No new runtime routes, schema fields, or storage backends. Everything a
  Spaces UX needs is already in `JobService`, `ArtifactService`, the audit
  log, the governed-memory store, and the HITL funnels.
- No shared schema in `swarmkit-schema`. A Notion-like Space, a Linear-like
  Space, and a chat-thread-like Space will model their body differently;
  freezing one shape in the schema is the wrong trade.
- Not an argument for abstracting Spaces across apps. Two apps that both
  "have Spaces" probably want different fields; the shared layer is the
  five primitives below, not a `Space` object.

## The five primitives

### 1. Durable handoff between runs — `correlation_id` + `ArtifactService`

Every run can carry a `correlation_id` the app chooses. The runtime stores
it on `jobs.correlation_id`, so `/jobs/history?correlation_id=<space-id>`
surfaces every run that happened inside that Space. Writes that need to
persist beyond one run go through `ArtifactService.put_yaml` /
`create_from_yaml`, which validates + rolls back on failure and keyes
artifacts by filename inside the workspace — the Space id becomes part of
the filename convention (`spaces/<space-id>/note.md`,
`spaces/<space-id>/plan.yaml`, etc).

**App does**: generates a Space id (ULID or whatever), passes it as
`correlation_id` on every run it starts for that Space, writes
Space-scoped artifacts under a predictable path prefix.

**Runtime does**: already-shipping durable job rows and artifact store.

### 2. Human↔agent approval — `approvals:resolve` scope + HITL Funnels

Every write inside a Space that needs a human sign-off is gated by a Funnel
whose `approve.rules` require the Space's owner role to resolve. The portal
+ the app's own UI surface the pending approvals through the existing
HITL endpoints; the runtime refuses to proceed until the resolution lands.

**App does**: attaches a Funnel to any archetype that performs a Space
write. Scopes the Funnel's `approve.rules` to the Space owner (via a
`labels.space_owner` convention or similar; nothing structural).

**Runtime does**: the Funnel enforcement already shipped. The
`approvals:resolve` scope is already structurally reserved for human
identities (design §8.7), so an agent cannot approve its own write.

### 3. Audit anchor — `/audit?correlation_id=…&kind=artifact`

The audit log is append-only (design §8.3). Filtering it by the Space's
`correlation_id` gives a complete, immutable timeline of what happened
inside the Space — reads, writes, decisions, approvals. The app renders
this as the Space's activity feed; no additional storage.

**App does**: calls `/audit?correlation_id=<space-id>` (or filters the
Space's own activity view by that id).

**Runtime does**: already provides correlation-filtered audit queries.

### 4. Agent-readable mirror — governed-memory namespace `space:<id>`

Agents running inside a Space should not have to replay the audit log.
Instead, the app writes a condensed, agent-readable mirror to the governed
memory under a namespace keyed by the Space id — `space:<id>:summary`,
`space:<id>:open-items`, `space:<id>:recent-decisions`. The governed-memory
store already supports namespaced reads, append-only change-logs, and the
reconcile-decision pattern from
[`governed-memory.md`](governed-memory.md).

**App does**: on every meaningful Space event, writes or updates the
corresponding memory entry. Reads happen through the standard
memory-reader skill, no new primitive.

**Runtime does**: governed-memory is the shipped mechanism.

### 5. Retention — app-side TTL sweep

A Space that nobody has touched in N days is a candidate for archive or
deletion. The runtime holds the artifacts and the audit; the app decides
when to let a Space go. A nightly sweep job marks eligible Spaces, waits
for an owner's explicit archive click, and moves the Space's artifacts +
memory namespace into a `spaces/_archived/` tree. The audit stays — it is
append-only.

**App does**: owns the lifecycle. Nothing is retention-aware in the
runtime; nothing needs to be.

**Runtime does**: nothing. The audit's append-only property is the
authoritative floor; apps that need lossy retention apply it on top.

## Worked example — DOT's 'Daily Brief' Space

The DOT companion app ([`reference/apps/dot/`](../../reference/apps/dot/))
runs a morning-brief topology once a day. Each run is one Space.

### Composition

| primitive | DOT concretely does |
|---|---|
| Space id | `brief:<YYYY-MM-DD>` — one Space per morning. |
| Durable handoff | Every run started for that day (morning aggregation, per-item handle-item delegations, follow-up checks) is launched with `correlation_id="brief:2026-10-06"`. |
| Artifact storage | Attachment-scale outputs (ranked list snapshot, draft email bodies) go through `ArtifactService.create_from_yaml` under `spaces/brief-2026-10-06/*.yaml`. |
| Approval | Any outbound write (gmail.send_reply, calendar.update_event) is gated by the `dot:send` Funnel. Owner is the only role on `approve.rules`. |
| Audit anchor | DOT's `/activity` view filters `/audit?correlation_id=brief:2026-10-06` to render the day's timeline — aggregation call, triage decision, each delegation, each approval. |
| Agent-readable mirror | DOT writes `space:brief:2026-10-06:summary` after each turn — "3 items surfaced, 1 drafted, 0 sent" — so a follow-up run of handle-item on a surviving item can read the day's state without replaying every event. |
| Retention | DOT's nightly sweep marks any brief older than 90 days as archive-candidate. The owner can click "archive today's brief" at any time; the sweep is just the automation. Audit stays. |

### What this gets DOT

- **Resumability.** Owner closes DOT at 09:12 with one draft reply pending.
  Opens it at 14:30; the Space is still there — the draft artifact survives,
  the approval seam is still open, the activity feed shows what has
  happened since. No special DOT code; `correlation_id` was all that
  needed to persist.
- **Shareability (future).** If DOT grows a second user per workspace, the
  Space's role-scoped approvals already model who can see / sign off. The
  Funnel's `roles:` list becomes an invitation surface.
- **Explainability.** Every entry in the brief has an audit trace behind it
  that the owner can expand. The portal's existing trace view works
  unchanged.
- **Agent memory without conversation replay.** The next day's brief can
  pull `space:brief:2026-10-05:summary` to reason over yesterday's
  outstanding items without re-reading an entire inbox.

### Code shape

```ts
// app/lib/space-id.ts
export function todaysSpaceId(): string {
  return `brief:${new Date().toISOString().slice(0, 10)}`;
}

// app/api/brief/run/route.ts
export async function POST(_req: NextRequest): Promise<Response> {
  const spaceId = todaysSpaceId();
  const r = await swarmkit.post("/run/morning-brief", {
    input: "",
    correlation_id: spaceId,
    labels: { space_owner: "owner", space_kind: "brief" },
  });
  return Response.json({ space_id: spaceId, job_id: r.job_id });
}

// app/space/[id]/page.tsx — the Space view
async function Space({ params }: { params: { id: string } }) {
  const audit = await fetch(
    `/api/audit?correlation_id=${params.id}&kind=artifact`,
  );
  const mirror = await fetch(`/api/memory/space:${params.id}:summary`);
  const pendingApprovals = await fetch(
    `/api/approvals?correlation_id=${params.id}`,
  );
  return <SpaceView audit={audit} mirror={mirror} approvals={pendingApprovals} />;
}
```

Three fetches, no new runtime concept, every API already exists.

## When to reach for this pattern

- The app needs a scope narrower than a workspace but wider than a single
  run.
- Multiple runs collaborate on one outcome and the user wants a stable
  landing page for that outcome.
- A human approval needs to persist across runs — one run proposes, a
  later run (after the human says yes) executes.
- The agent needs a condensed snapshot of a long-running interaction's
  state without replaying every event.

## When NOT to reach for it

- A one-shot task — correlation_id already covers "every call in this
  conversation" without needing a Space surface.
- A pure cache. A Space is a user-facing concept with its own URL; if the
  only consumer is other agents, it is a memory namespace, not a Space.
- Something the user never looks at. Spaces earn their complexity by being
  a shared, visible surface; if nobody views the Space view, there is no
  Space.

## Why not a runtime primitive

Three apps that each have "Spaces" will model the body differently
(Notion-like page, Linear-like board, chat-thread-like canvas) and will
need app-specific fields the runtime cannot sanely accommodate. The
five primitives above are the stable surface across all three shapes;
baking a `Space` type into the runtime would freeze one app's choices
into a surface three apps would then subset or extend.

The right abstraction level is docs — this note — so an app author
knows which primitives to compose and how, with a worked example to
copy from. No new routes, no new schema, no new storage.

## See also

- `design/details/governed-memory.md` — the memory shape the mirror uses.
- `design/details/credential-service.md` — per-user credentials when a
  Space is owned by a specific identity.
- `reference/apps/dot/` — the worked example from § DOT's Daily Brief Space.
- Issue #1017 — this note's tracker.
