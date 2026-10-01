# DOT quickstart

End-to-end: install, mint credentials, boot DOT against either the bundled mock runtime or
a real SwarmKit runtime, and walk the owner's daily path.

The design lives in [`design/details/dot-app.md`](../design/details/dot-app.md). This page
is the operator's guide.

## Prerequisites

- Node 20+ and pnpm (`corepack enable` picks up pnpm 9.x from the workspace)
- Optional: Python 3.11+ and `uv` for running the real runtime
- Optional: Docker + `just` for the compose path

```bash
git clone https://github.com/delivstat/swarmkit
cd swarmkit
pnpm install
```

## Path A — standalone (no runtime, full demo)

The fastest way to see every page. A small mock runtime emulates the slice of the real
runtime DOT uses (fast-lane MCP invocation, handle-item SSE, OAuth with a fake Google
consent page, workspace config, audit log, usage aggregates).

```bash
cp reference/apps/dot/.env.example reference/apps/dot/.env
printf '%s' 'dot' | node reference/apps/dot/scripts/hash-password.mjs
# Copy the printed value — including the surrounding double quotes and the escaped $ — into
# DOT_OWNER_PASSWORD_HASH= in .env. Set DOT_OWNER_USERNAME=owner and SESSION_SECRET to a
# 48-character random string:
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Boot both processes:

```bash
pnpm --filter @swarmkit/dot mock-runtime &   # → :8000
pnpm --filter @swarmkit/dot dev              # → :3400
```

Open http://localhost:3400 → sign in → walk the pages:

1. **/** — the brief. Five fixture items, two calendar + three Gmail. Tap any card to open
   the detail route.
2. **/item/{id}** — tap *Prep* on the OEM-sync card and watch two SSE progress frames
   stream in before the structured prep-note artefact renders. *Draft reply* on the cost
   thread renders an editable email form. Use the chat pane on the right (or bottom-sheet
   on mobile) to ask a scoped question.
3. **/connections** — click *Connect* on Gmail. The browser lands on the mock Google
   consent screen; click *Allow* and you return to `/connections?connected=gmail` with
   status flipped to **Connected**. The real flow is identical — the runtime does the
   token exchange and 302s back to DOT.
4. **/settings** — change the brief time or ranker cap; the inline *Saving…* → *Saved*
   indicator confirms the debounced PATCH went through.
5. **/activity** — four recent runs; the last one is red, expand to see the per-archetype
   error.
6. **/usage** — three stat tiles, a 30-day cost chart, and a provider/topology breakdown
   toggle.

## Path B — real runtime

Run the SwarmKit runtime with the DOT workspace mounted, then point DOT at it:

```bash
# Terminal 1 — runtime
uv run swarmkit serve --workspace reference/workspaces/dot --port 8000

# Terminal 2 — DOT (same .env as Path A, but set SWARMKIT_RUNTIME_TOKEN)
# Mint a token for DOT once the CLI lands (design §Runtime dependencies):
#   swarmkit auth issue-client-token dot --owner=<email> --lifetime=90d
# In the interim, generate one through the portal under IAM → clients.

pnpm --filter @swarmkit/dot dev
```

Connect your real Gmail + Calendar from `/connections`; the runtime stores encrypted
per-user tokens in its `oauth_tokens` table. DOT never touches them.

## Path C — Docker compose

```bash
cp reference/apps/dot/.env.example reference/apps/dot/.env
# fill in all four required values; set SWARMKIT_RUNTIME_URL for the external case
just dot-up                                   # DOT alone
# or
just dot-up-all                               # DOT + runtime side-by-side
```

Logs and teardown:

```bash
just dot-logs                                 # tail DOT
just dot-down
```

The published image is `ghcr.io/delivstat/dot-app:<tag>`; built and pushed on `dot-v*`
tags via `.github/workflows/dot-image.yml`.

## Troubleshooting

- **Login returns 401 even with the right password.** Check that `DOT_OWNER_PASSWORD_HASH`
  is wrapped in double quotes with every `$` escaped as `\$` — Next.js's env loader
  expands unquoted `$VAR` segments. The `hash-password.mjs` helper prints the value
  pre-escaped; paste exactly what it prints.
- **Brief shows an "empty" state.** The mock serves a fixed set; make sure the mock
  runtime is up at :8000 (`curl http://localhost:8000/health`).
- **Stale JS after a dependency change.** `rm -rf reference/apps/dot/.next` and restart
  the dev server. Running `pnpm build` while `pnpm dev` is also serving corrupts the dev
  cache.
- **Session expires every reload.** `SESSION_SECRET` is rotating between restarts
  (shouldn't happen if it's in `.env`) — same discipline as the fleet panel's
  `SWARMKIT_CONTROL_PLANE_SECRET_KEY`. Rotate deliberately, not every boot.

## What's deliberately not here

- Push notifications, email digests, webhook triggers to the app — the brief fires on
  the workspace's scheduled trigger.
- Multi-user / tenancy. DOT is owner-only by design.
- Rich workspace editing (archetype YAML, topology structure, credential-store contents,
  MCP server registration). Those stay in the SwarmKit portal.
- Budget alerting — the Usage page shows numbers but doesn't notify when you blow one.
  That's a Sprint 2+ concern with its own design.
