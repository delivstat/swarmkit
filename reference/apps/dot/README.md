# DOT — the reference app for the DOT workspace

The standalone Next.js companion for `reference/workspaces/dot/`. Owner-only, mobile-first,
served as a Docker image. Full design at [`design/details/dot-app.md`](../../../design/details/dot-app.md).

## Status

**Auth** (commit 2 of PR 5). `/login` + `/api/auth/{login,logout,whoami}` +
signed-cookie sessions + owner-scoped middleware. Copy `.env.example` to `.env`,
mint a password hash with `scripts/hash-password.mjs`, and `pnpm dev` gets you to
a real sign-in round-trip. Every non-`/login` route redirects to sign-in.

Next commits (per the design note's split):

3. **Today's brief (static)** — card stack against a hardcoded fixture.
4. **Today's brief (live)** — wires to the runtime's `POST /api/mcp/{server_id}/invoke` (shipped
   in v1.260.0) for the fast-lane pre-fetch.
5. **Per-item detail** — buttons + scoped chat + result panel.
6. **Connections** — inline OAuth handshake with `return_to`.
7. **Settings** — workspace-config projection.
8. **Activity** — audit-log projection.
9. **Usage & cost** — stat tiles + 30-day chart + breakdowns.
10. **Dockerfile + compose + a one-command boot target** — deployment surface.
11. **README + `docs/dot-quickstart.md`** — end-to-end quickstart.

## Local dev

```bash
cp reference/apps/dot/.env.example reference/apps/dot/.env
# fill in DOT_OWNER_PASSWORD_HASH + SESSION_SECRET (instructions in the file)
pnpm install
pnpm --filter @swarmkit/dot dev
# → http://localhost:3400 → /login
```

Mint a password hash without leaking to shell history:

```bash
printf '%s' 'your password' | node reference/apps/dot/scripts/hash-password.mjs
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
