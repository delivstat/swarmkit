# dots — Phase 3 scaffold

Multi-Dot chat reference app on CopilotKit + AG-UI. See
[`design/details/dots-app.md`](../../../design/details/dots-app.md) for the full design.

**Phase 3 scope (this commit):** scaffold + one Dot (Morning Brief) streaming end-to-end
against a mock AG-UI runtime. No real `swarmkit serve` wire yet — that's Phase 3b.

## Prereqs

```bash
cd reference/apps/dots
pnpm install

export SESSION_SECRET="$(openssl rand -hex 32)"
export DOTS_OWNER_PASSWORD_HASH="$(node scripts/hash-password.mjs 'change-me')"
```

## Standalone demo (mock runtime, no Python)

Two terminals:

```bash
# terminal 1
pnpm mock-runtime

# terminal 2
SWARMKIT_URL=http://127.0.0.1:4100 pnpm dev
```

Open http://localhost:3500, log in with your owner password, and chat with the Morning Brief
Dot. The mock streams a canned brief back one token at a time over AG-UI SSE.

## Against a live swarmkit serve (Phase 3b)

```bash
# terminal 1 — in the main repo
cd ../../..
SWARMKIT_PROVIDER=mock uv run swarmkit serve examples/hello-swarm/workspace

# terminal 2
SWARMKIT_URL=http://127.0.0.1:8000 pnpm dev
```

## Structure

```
app/
  dots/[dotId]/        # one chat surface per Dot
  api/
    auth/              # reused from reference/apps/dot
    ag-ui/             # proxy → POST /api/ag-ui/run
  login/               # reused from reference/apps/dot
components/
  sidebar/dots-list    # Dot picker
  chat/dot-chat        # thin SSE chat view (CopilotChat lands in Phase 3b)
lib/
  dots.config.ts       # app-local Dot declarations
  ag-ui-client.ts      # SSE decoder for AG-UI frames
  session.ts           # Web Crypto HMAC cookie (shared discipline with DOT)
  password.ts
mocks/
  mock-runtime.mjs     # tiny node http server speaking AG-UI
```

## Not yet shipped

- `<CopilotChat>` from `@copilotkit/react-ui`. Phase 3b after the agent-adapter spike.
- Generative-UI renderers. Phase 3b, via the fenced-JSON stopgap called out in the design.
- Live wire to `swarmkit serve`. Phase 3b.
- Second Dot + provider Dot (GitHub). Phase 4.
- Connections / usage pages. Phase 4b.
