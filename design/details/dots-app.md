# dots — a multi-Dot reference app on CopilotKit + AG-UI

**Status:** Phase 2 design of #1018. Phase 1 (AG-UI protocol surface, #1016) landed in #1020.
**Lives at:** `reference/apps/dots/`.

## Goal

A reference app that proves two things at once:

1. **SwarmKit speaks AG-UI** — any CopilotKit frontend can drive a governed SwarmKit backend
   with no bespoke adapter.
2. **"Dot" is a usable product noun** — a Dot is one chat surface wired to one topology and
   one role, and users talk to the Dot by name, not to "the topology."

The app is not a replacement for `reference/apps/dot/` (singular, the card-stack personal workflow).
Both ship side by side; Phase 5 of #1018 picks the survivor after real use.

## Non-goals

- **Not reimplementing DOT's UX.** DOT is a non-chat card stack; this is a chat app. Different
  product shape, different reference value.
- **Not rebuilding what CopilotKit already does well.** Streaming, generative UI, HITL
  components, shared state — reuse `<CopilotChat>` + `useCopilotAction` instead.
- **Not inventing a Dot runtime primitive.** A Dot is a thin frontend grouping (name, icon,
  topology id, role label, generative-UI schema set). The runtime still runs topologies.
- **Not touching SwarmKit core.** No new schema in `packages/schema`, no new routes beyond
  what #1016 shipped. If #1014 (`kind: Dot`) lands later the app adopts it; until then dots
  are declared in a frontend-local `dots.config.ts`.
- **Not shipping voice or multi-channel in v1.** Phase 4 extensions.

## Positioning next to DOT

|                      | DOT (singular, exists)                 | dots (plural, this design)                |
|----------------------|----------------------------------------|-------------------------------------------|
| Shape                | Card-stack personal workflow           | Multi-Dot chat UX                         |
| Primary UI           | Hand-rolled Next.js components         | CopilotKit `<CopilotChat>` + generative UI |
| Runtime protocol     | REST + custom SSE proxy                | AG-UI over SSE (`POST /api/ag-ui/run`)    |
| Mental model         | One screen per topology                | One Dot per role                          |
| HITL                 | Approval cards inline in brief         | AG-UI approval events → CopilotKit action |
| Settings / activity  | Full operator surface                  | Minimal (settings, connections, usage)    |
| Auth                 | scrypt + HMAC session cookie           | Same (reused verbatim)                    |

## Licensing

CopilotKit's `@copilotkit/react-core`, `@copilotkit/react-ui`, and `@copilotkit/runtime` are all
MIT (monorepo-wide LICENSE at `github.com/CopilotKit/CopilotKit`). MIT is Apache-2.0 compatible
as a dependency. No CLA for consumers; no field-of-use restrictions. CopilotCloud (their SaaS
tier) is separate; nothing we depend on routes through it.

## The Dot primitive, as data

A Dot is declared in `reference/apps/dots/dots.config.ts` (one TypeScript module, not a schema
artifact — see non-goal above):

```ts
export const dots: Dot[] = [
  {
    id: "morning-brief",
    name: "Morning Brief",
    role: "Daily brief curator",
    icon: "sunrise",
    topology: "morning-brief",
    greeting: "Ready for today's brief. Shall I pull it?",
    renderers: ["brief-item", "retrieved-thread"],
  },
  {
    id: "handle-item",
    name: "Handle Item",
    role: "Delegate for a specific brief item",
    icon: "mail-reply",
    topology: "handle-item",
    renderers: ["email-draft", "prep-note"],
  },
];
```

If #1014 lands, this becomes `dots/*.yaml` artifacts loaded through the shared validator — the
frontend changes but the UX does not.

## Shape of the app

```
reference/apps/dots/
├── app/
│   ├── (auth)/login/           # reused from DOT
│   ├── (chat)/
│   │   ├── layout.tsx          # sidebar + <CopilotKit> provider
│   │   ├── page.tsx            # /dots → first dot
│   │   └── [dotId]/page.tsx    # /dots/[id] → chat surface
│   ├── settings/               # minimal
│   ├── connections/            # reused from DOT
│   └── usage/                  # reused from DOT
├── components/
│   ├── sidebar/dots-list.tsx
│   ├── chat/dot-chat.tsx       # <CopilotChat> + agent config
│   └── renderers/              # one per Dot payload shape
│       ├── brief-item.tsx
│       ├── email-draft.tsx
│       ├── prep-note.tsx
│       └── retrieved-thread.tsx
├── lib/
│   ├── session.ts              # reused from DOT (Web Crypto HMAC)
│   ├── ag-ui-client.ts         # thin fetch to POST /api/ag-ui/run
│   └── dots.config.ts
├── mocks/                      # same mock-runtime pattern as DOT, speaks AG-UI
├── Dockerfile · docker-compose.yml
└── README.md
```

## How a Dot talks to swarmkit serve

1. User sends a message in `<CopilotChat>` for Dot `morning-brief`.
2. CopilotKit's agent adapter — custom, backed by `ag-ui-client.ts` — posts to
   `/api/ag-ui/run` with:
   ```json
   {
     "threadId": "<dotId>:<sessionId>",
     "messages": [...prior turns, this user turn],
     "context": { "topology": "morning-brief" }
   }
   ```
3. SSE events stream back. `TextMessageContent` deltas feed the chat bubble.
   `RunFinished` closes the turn.
4. v2 (follow-up): tool-call events render into generative-UI cards; approval events pause
   the stream and surface `useCopilotAction`-driven approve/deny components.

v1 of the dots app is bounded by v1 of the AG-UI protocol (Lifecycle + Messages only). Rich
in-chat renderings land after the structured event bus does — see `docs/notes/ag-ui-integration.md`
Gotcha 1.

## Generative UI, pre-event-bus

Until tool-call events ship, the renderers trigger from **string shape** in the assistant's
final answer. The topology emits a fenced ```json block tagged with the renderer name:

~~~
Here's today's brief:

```dots:brief-item
{ "subject": "Weekly review", "sender": "...", "action": "reply" }
```
~~~

The chat view splits on these fences and renders each payload through its matching component.
It's a stopgap: once ToolCall events land, the topology emits structured calls and the fences
go away. This is explicit tech debt — the design note names it so the follow-up PR can remove it.

## HITL — v1 approach

v1 defers HITL to the operator portal — the sidebar links to `/approvals` on the operator UI,
and the chat shows a "waiting for approval" status that polls `GET /jobs/{id}`. Full in-chat
approval needs AG-UI's approval event; it ships with the event bus.

## Auth

Reused verbatim from DOT:

- scrypt-hashed owner password at install time
- HMAC-SHA256 session cookie via Web Crypto (Edge-middleware compatible)
- Same `/login`, same middleware, same password-rotation flow

## Deployment

- `Dockerfile` → `node:22-slim` + Next.js standalone output
- `docker-compose.yml` → `dots-app` + `swarmkit` services, same network as DOT's compose
- `just demo-dots-app` — brings both up, prints `http://localhost:3002` (DOT keeps 3001)

## API shape

The app only talks to swarmkit serve through endpoints that already exist:

| Method | Path                        | Purpose                                     |
|--------|-----------------------------|---------------------------------------------|
| POST   | `/api/ag-ui/run`            | Drive a Dot's chat (per #1016)              |
| GET    | `/jobs/{id}`                | Poll approval status (v1 HITL stopgap)      |
| GET    | `/api/oauth/my-credentials` | Connections page `used_by`                  |
| POST   | `/api/oauth/connect`        | Begin OAuth flow                            |
| GET    | `/topologies`               | Validate Dot config at startup              |

No new runtime routes. If one is tempting, it goes in a separate SwarmKit PR with its own
design note first.

## Test plan

- **Unit:** `dots.config.ts` validation (every `topology` exists in `/topologies`); fence-splitter
  parser; AG-UI client (events decoded in order, abort on `RunError`).
- **Integration:** Playwright test driving the mock-runtime happy path (login → send a message
  → see streaming reply → see a generative-UI card). One flow is enough for the reference
  app; this isn't a product-quality test suite.
- **Live:** `just demo-dots-app` must produce a visible brief in the Morning Brief Dot against
  `examples/hello-swarm/workspace`.

## Demo plan

- `just demo-dots-app` — compose up, open browser, send "What's my brief?" to Morning Brief.
- Transcript saved to `reference/apps/dots/README.md` (ascii + screenshot).
- PR body includes a 30-second recording.

## Phased delivery (sub-issues)

1. **Phase 3** — scaffold (`reference/apps/dots/`): Next.js shell + `<CopilotKit>` provider +
   auth reuse + mock AG-UI runtime. One Dot (Morning Brief) chats end-to-end against mock. ~1 PR.
2. **Phase 3b** — live wire: swap mock for `swarmkit serve`; `/api/ag-ui/run` round-trip; one
   fence-renderer (`brief-item`). ~1 PR.
3. **Phase 4** — second Dot (Handle Item), plus a provider Dot (GitHub). ~1 PR.
4. **Phase 4b** — connections + usage pages reused from DOT. ~1 PR.
5. **Phase 5** — DOT vs dots decision issue, no code.

Each phase is one issue, one branch, one PR, each ending in a working demo.

## Open questions

- **Q1.** Does CopilotKit's agent adapter accept our AG-UI event vocabulary directly, or do we
  need a thin translation shim? Phase 3 kickoff — spike before scaffold.
- **Q2.** `dots.config.ts` vs `dots/*.yaml` — the latter is nicer and matches SwarmKit's shape
  but blocks on #1014. Pick config.ts for Phase 3 and migrate when #1014 lands.
- **Q3.** Approval-polling vs sidebar-link for HITL — the sidebar-link is cheap, the polling is
  only slightly more; both are temporary stopgaps until AG-UI approval events ship. Pick
  sidebar-link for v1 to keep the scaffold diff small.

## Risks

- **CopilotKit SDK churn (<1.0).** Pin exact versions; absorb breakage in one PR per upstream.
- **Styling.** `<CopilotChat>` has strong defaults; brand theming lives in a follow-up PR.
- **Fence-renderer stopgap outliving its welcome.** Delete it the moment ToolCall events ship.
