# DOT — the reference app for the DOT workspace

The standalone Next.js companion for `reference/workspaces/dot/`. Owner-only, mobile-first,
served as a Docker image. Full design at [`design/details/dot-app.md`](../../../design/details/dot-app.md).

## Status

**Scaffold** (commit 1 of PR 5). `pnpm dev` boots a blank shell of every route; each route
carries a placeholder saying which subsequent commit will fill it in.

Next commits (per the design note's split):

2. **Auth** — `/login` form, `/api/auth/*` routes, session cookie helpers.
3. **Today's brief (static)** — card stack against a hardcoded fixture.
4. **Today's brief (live)** — wires to the runtime's `POST /api/mcp/{server_id}/invoke` (shipped
   in v1.260.0) for the fast-lane pre-fetch.
5. **Per-item detail** — buttons + scoped chat + result panel.
6. **Connections** — inline OAuth handshake with `return_to`.
7. **Settings** — workspace-config projection.
8. **Activity** — audit-log projection.
9. **Usage & cost** — stat tiles + 30-day chart + breakdowns.
10. **Dockerfile + compose + `just dot-up`** — deployment surface.
11. **README + `docs/dot-quickstart.md`** — end-to-end quickstart.

## Local dev (when there is something to see)

```bash
pnpm install
pnpm --filter @swarmkit/dot dev
# → http://localhost:3400
```

## Layout

```
reference/apps/dot/
  app/                # Next.js App Router — one directory per route
    layout.tsx        # HTML shell + Geist + neutral-dark palette
    globals.css       # shadcn tokens + mobile-first font sizing
    page.tsx          # /
    login/page.tsx
    connections/page.tsx
    settings/page.tsx
    activity/page.tsx
    usage/page.tsx
    item/[id]/page.tsx
  lib/                # cn() and (later) session, swarmkit-client
  components/         # (later) ItemCard, ActionButtons, ChatPane, …
  next.config.ts      # output: 'standalone' for Docker
  package.json
```

## Not the SwarmKit portal

DOT is a peer to `packages/ui/`, not part of it. Different origin, different auth surface,
different UX. The portal is for platform observability; DOT is for the daily driver. See
`design/details/dot-app.md` for the full split.
