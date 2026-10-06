# DOT — the reference app for the DOT workspace

The standalone Next.js companion for [`reference/workspaces/dot/`](../../workspaces/dot/).
Owner-only, mobile-first, served as a Docker image. Full design in
[`design/details/dot-app.md`](../../../design/details/dot-app.md).

DOT renders the morning brief, exposes per-item delegation (buttons + scoped chat), and
hosts the connections / settings / activity / usage pages the owner needs for a normal day.
The SwarmKit portal stays the debug/power surface — a normal day the owner never opens it.

**End-to-end quickstart** lives at [`docs/dot-quickstart.md`](../../../docs/dot-quickstart.md).

## What you can see today

- **Today's brief** at `/` — a ranked card stack. Fetched through the runtime's fast-lane
  MCP invocation (`POST /api/mcp/{server_id}/invoke`); zero LLM tokens on the primary path.
  Refresh triggers an on-demand rerun. Polls every 30 s and on window focus.
- **Per-item detail** at `/item/[id]` — buttons dispatch to the handle-item topology
  through SSE; scoped chat on the right rail (desktop) or stacked below (mobile); a typed
  result panel renders email drafts, prep notes, retrieved threads or acknowledgements.
- **Connections** at `/connections` — Gmail + Google Calendar, inline OAuth with
  `return_to` so the browser stays on DOT throughout the handshake. DOT never sees a
  Google token; the runtime's OAuth store is the single credential authority.
- **Settings** at `/settings` — brief schedule, model provider + name, ranker item cap.
  Debounced autosave to the runtime's workspace-config surface. DOT never edits
  `workspace.yaml` directly.
- **Activity** at `/activity` — recent runs for `morning-brief` + `handle-item`, each row
  expands for per-archetype timings and errors. Linked out to the SwarmKit portal for the
  full audit trail.
- **Usage & cost** at `/usage` — today / month-to-date / last-30-days stat tiles, a
  30-day inline SVG bar chart, and a provider/topology breakdown with a toggle.

## Local dev — fastest path (no runtime)

A small mock runtime ships at `mocks/mock-runtime.mjs`; it serves everything DOT needs for
a demo (MCP invocations, handle-item SSE, OAuth with a fake Google consent page,
workspace-config, audit log, usage aggregates).

```bash
cp reference/apps/dot/.env.example reference/apps/dot/.env
# fill in DOT_OWNER_USERNAME + DOT_OWNER_PASSWORD_HASH + SESSION_SECRET. The hash helper:
printf '%s' 'your password' | node reference/apps/dot/scripts/hash-password.mjs

pnpm install
# Two processes — point SWARMKIT_RUNTIME_URL at http://localhost:8000 (default in .env.example).
pnpm --filter @swarmkit/dot mock-runtime &
pnpm --filter @swarmkit/dot dev
# → http://localhost:3400 → /login
```

## Local dev — against the real runtime

```bash
# Terminal 1 — SwarmKit runtime (expects reference/workspaces/dot loaded).
uv run swarmkit serve --workspace reference/workspaces/dot --port 8000

# Terminal 2 — DOT.
pnpm --filter @swarmkit/dot dev
```

Mint a runtime bearer token with `swarmkit auth issue-client-token dot --owner=<email>`
(pending — see design §Runtime dependencies) or configure one through the portal in the
interim. Set `SWARMKIT_RUNTIME_TOKEN` in `.env`.

## Production — Docker + compose

```bash
cp reference/apps/dot/.env.example reference/apps/dot/.env
# fill in the four required values; set SWARMKIT_RUNTIME_URL to wherever your runtime lives
just dot-up                                    # DOT alone
# or:
just dot-up-all                                # DOT + bundled runtime on the same host
```

The image publishes to `ghcr.io/delivstat/dot-app:<tag>` on `dot-v*` tags via
`.github/workflows/dot-image.yml`.

## Layout

```
reference/apps/dot/
  app/                 # Next.js App Router — one directory per route
    layout.tsx         # HTML shell + Geist + neutral-dark palette
    providers.tsx      # QueryClientProvider
    page.tsx           # /
    brief-view.tsx     # React Query-wired today's-brief view
    item/[id]/         # per-item detail (buttons + chat + result panel)
    connections/       # /connections + inline OAuth handshake
    settings/          # /settings + debounced autosave
    activity/          # /activity + per-run detail
    usage/             # /usage + 30-day chart + breakdown
    api/               # route handlers (auth, brief, items, connections, …)
  components/          # ItemCard, ActionButtons, ChatPane, ResultPanel, …
  lib/                 # session, password, swarmkit-client, brief, sse, …
  mocks/               # the standalone mock runtime
  scripts/             # hash-password.mjs
  tests/               # vitest unit tests
  Dockerfile           # multi-stage, Next.js standalone output
  docker-compose.yml   # default + all-in-one profiles
```

## The daily brief is a Space

Each day's brief is a **Space** — a stable, auditable, durable surface composed from
primitives SwarmKit already ships (correlation_id + ArtifactService + the audit log +
governed-memory + a Funnel). Space id is `brief:<YYYY-MM-DD>`; every run started for
that day (morning aggregation, per-item delegations, follow-ups) carries that id as
its `correlation_id`, so `/activity` can render a complete timeline from the audit
log and the owner can close + reopen the app without losing a draft reply.

The full pattern (five primitives + when to reach for it, with DOT as the worked
example) is in [`design/details/spaces-pattern.md`](../../../design/details/spaces-pattern.md).

## Not the SwarmKit portal

DOT is a peer to `packages/ui/`, not part of it. Different origin, different auth surface,
different UX. The portal is for platform observability; DOT is for the daily driver. See
[`design/details/dot-app.md`](../../../design/details/dot-app.md) §Part 1 for the full split.
