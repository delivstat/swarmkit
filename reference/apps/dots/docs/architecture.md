# dots — architecture

A reference app that shows a human talking to many SwarmKit agents from one
chat surface, and scoping those conversations into **Spaces** that group work
across any coworker involved in it. This doc covers the whole app end to end —
what each file is for, how a request flows from the browser to a running swarm,
and where every primitive it uses lives in the runtime.

The authoritative design is in `design/details/dots-app.md` and
`design/details/spaces-pattern.md`. If this doc disagrees with the code, the
code wins — fix the doc.

---

## 1. The product

A **Dot** is a chat surface: one role, one SwarmKit topology, a set of
renderers for structured payloads the agent emits inline. The user picks a Dot
from the sidebar and chats with it; the agent streams tokens and tool calls
back through AG-UI SSE; the browser renders a familiar chat UI (CopilotKit).

The **Author Dot** is the entry point to adding more Dots: the user chats with
it ("I want a coworker that triages my inbox each morning"), and it writes a
new topology file + registers a new Dot. Nobody edits YAML by hand to get a
coworker.

A **Space** is a scope broader than one chat — "launch Q4 campaign", "refactor
auth module". Inside a Space the user can switch between any Dot in the app;
every run started under the Space carries one `correlation_id`, so the activity
timeline, the audit log and the governed-memory namespace all key off a single
scope regardless of which Dot did the work. See §6.

---

## 2. The layout

```
reference/apps/dots/
├── app/
│   ├── (app)/                 # authed shell — sidebar + chat canvas
│   │   ├── layout.tsx         # session guard; loads Dots + Spaces for sidebar
│   │   ├── page.tsx           # landing (defaults to the Author Dot)
│   │   ├── dots/[dotId]/      # one chat surface per Dot
│   │   ├── spaces/            # Space list, create, three-panel view
│   │   ├── connections/       # OAuth-ish provider wiring (stub)
│   │   └── usage/             # token-and-cost view (stub)
│   ├── api/
│   │   ├── auth/              # login + session cookie
│   │   ├── ag-ui/route.ts     # AG-UI proxy to swarmkit serve
│   │   ├── dots/              # list/create/update local Dots
│   │   ├── spaces/            # list/create Spaces, activity feed
│   │   ├── topologies/        # browse topologies swarmkit knows
│   │   └── connections/, usage/
│   ├── login/                 # /login → session cookie
│   └── globals.css            # shadcn/ui + Geist tokens
├── components/
│   ├── sidebar/dots-list.tsx  # Dots section, Spaces section, utility links
│   └── chat/dot-chat.tsx      # CopilotChat wrapper for a single Dot
├── lib/
│   ├── dots.config.ts         # built-in Dots + local file loader
│   ├── spaces.config.ts       # Space type + correlationIdFor() + local file
│   ├── session.ts, password.ts, auth-env.ts
│   └── ag-ui-client.ts        # (used by the standalone mock runtime)
├── scripts/
│   ├── hash-password.mjs
│   ├── screenshot-*.mjs, capture-*.sh
│   └── screenshot-spaces.mjs  # the Spaces live-stack capture
├── mocks/
│   └── mock-runtime.mjs       # a tiny HTTP server that speaks AG-UI SSE
├── docs/
│   ├── architecture.md        # ← you are here
│   ├── spaces.md              # how to run the Spaces live stack
│   └── {screenshots, spaces-screenshots}/
└── demo-workspace/            # topologies the standalone demo loads
```

---

## 3. System diagram

```
                       ┌─────────────────────────────────┐
                       │           Browser                │
                       │  (CopilotKit chat + sidebar UI)  │
                       └───────────────┬──────────────────┘
                        HTTPS cookies │ SSE
                       ┌───────────────▼──────────────────┐
                       │  Next.js (dots app)              │
                       │  app/(app)/*  ·  app/api/*       │
                       │  session guard · AG-UI proxy     │
                       └───────┬─────────────────────┬────┘
          REST /api/spaces,    │                     │ POST /api/ag-ui/run
          /api/dots, ...       │                     │ SSE streamed back
                               │                     │
                       ┌───────▼─────────────────────▼────┐
                       │        swarmkit serve             │
                       │  AG-UI route · JobService ·       │
                       │  ArtifactService · AuditLog ·     │
                       │  governed-memory · ModelProvider  │
                       └───────────────┬──────────────────┘
                                       │ OpenRouter / Anthropic / Ollama
                                       ▼
                              (model providers)
```

Everything the browser does ends up as a REST or SSE call to the Next server.
The Next server is a thin seam: it enforces auth, injects per-request context
(active Dot's topology, active Space's correlation_id), and proxies to
`swarmkit serve`. **No business logic lives in the dots app.**

---

## 4. The data model

Two tiny TypeScript files on disk. They are the whole model.

```
lib/dots.config.ts                    lib/spaces.config.ts
┌─────────────────────────┐           ┌─────────────────────────┐
│ interface Dot {         │           │ interface Space {       │
│   id, name, role,       │           │   id, name,             │
│   icon, topology,       │           │   description,          │
│   greeting, renderers   │           │   owner, created_at     │
│ }                       │           │ }                       │
│                         │           │                         │
│ BUILT_IN_DOTS: Dot[]    │           │ BUILT_IN_SPACES: []     │
│                         │           │                         │
│ loadDots()  =           │           │ loadSpaces() =          │
│   local ∪ built-in      │           │   local ∪ built-in      │
│   (local wins by id)    │           │   (local wins by id)    │
└─────────────────────────┘           └─────────────────────────┘
       │                                       │
       ▼                                       ▼
 lib/dots.local.json                   lib/spaces.local.json
 (gitignored, user-created)            (gitignored, user-created)
```

**Why files, not a database:** the dots app is a reference, meant to be read
and copied. A sqlite instance would add infra; a JSON file is a data model
anyone can grok in one glance. Production deployments swap this out for
whatever store they prefer — the config module is the only seam.

A Space's `id` is also its correlation prefix: `correlationIdFor("launch-q4")
→ "space:launch-q4"`. That single convention is the whole Spaces mechanism;
every subsequent primitive — activity, audit, memory, approvals — composes on
top of that string without a line of new runtime code. See §6.

---

## 5. Request flow — a Dot chat turn

```
 1. User types in the chat composer and hits Enter
                 │
                 ▼
 2. CopilotKit calls HttpAgent.run(input) in the browser
                 │
                 │ POST /api/ag-ui?dotId=handle-item[&spaceId=launch-q4]
                 │ body: { threadId, runId, messages, context, state }
                 ▼
 3. app/api/ag-ui/route.ts
    ├── auth cookie check (middleware)
    ├── findDot(dotId)  →  dot.topology = "handle-item"
    ├── inject context.topology = "handle-item"
    ├── if spaceId:  inject context.correlation_id = "space:launch-q4"
    └── proxy POST → ${SWARMKIT_URL}/api/ag-ui/run
                 │ (SSE pipe)
                 ▼
 4. swarmkit serve  /api/ag-ui/run
    ├── caller_corr = context.correlation_id or thread_id      ◄── §6
    ├── labels      = { ag_ui.thread_id, [correlation_source] }
    ├── jobs.start(topology=handle-item, correlation_id, labels, ...)
    └── stream AG-UI events as SSE as the job runs
                 │
                 │ messages/tokens/tool-calls/state  as SSE
                 ▼
 5. Browser renders tokens into the chat panel in real time
```

Everything the chat does — attachments, tool-calls, approvals — rides on this
one pipe. The dots app has no second API path for a "special" message type.
That is deliberate: AG-UI is one shape; the server is one entry point;
observability is one query.

---

## 6. Spaces

A Space composes five existing runtime primitives into a shared surface. **It
adds no new runtime concept.** The design doc is
`design/details/spaces-pattern.md`; this is the applied form in the dots app.

### 6.1 What a Space _is_

```
                 ┌───────────────────────────────┐
                 │  Space: "Launch Q4 campaign"   │
                 │  id: launch-q4-campaign        │
                 │  correlation_id prefix:        │
                 │     space:launch-q4-campaign   │
                 └────────────────┬───────────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            │                     │                     │
            ▼                     ▼                     ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │ Handle Item chat │  │  Morning Brief   │  │   Author chat    │
   │   turn 1,2,3...  │  │  turn 1,2,3...   │  │   turn 1,2...    │
   └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
            │                     │                     │
            └─────────┬───────────┴──────────┬──────────┘
                      │                      │
                      ▼                      ▼
            runs tagged correlation_id = space:launch-q4-campaign
                      │                      │
            ┌─────────▼─────────┐  ┌─────────▼──────────┐
            │ /jobs/history     │  │ /audit             │
            │ ?correlation_id=… │  │ ?correlation_id=…  │
            └───────────────────┘  └────────────────────┘
```

### 6.2 How it threads through the stack

| Layer | What the Space does |
| --- | --- |
| **Browser** (SpaceView) | `HttpAgent` url is `/api/ag-ui?dotId=X&spaceId=Y`. One `HttpAgent` per `(Dot, Space)` pair. Chat picker swaps the active Dot without leaving the Space URL. |
| **Next proxy** (`app/api/ag-ui/route.ts`) | On every forwarded request: `context.topology = dot.topology`, and when `spaceId` is present `context.correlation_id = space:<id>`. |
| **Runtime** (`_routes_ag_ui.py`) | `caller_corr = context.correlation_id or thread_id`. If caller sent one, use it as the run's `correlation_id`; otherwise fall back to the thread id as before. Label `ag_ui.correlation_source` records which path was taken. |
| **Jobs** | Every run under this Space has the same `correlation_id`, so `/jobs/history?correlation_id=space:<id>` is the full timeline — regardless of which Dot kicked it off. |
| **Audit** | Audit events inherit `correlation_id`; `GET /audit?correlation_id=space:<id>` returns the full governance trail for the Space. |
| **Governed memory** | Writes under namespace `space:<id>` share across Dots in the Space. (Hook; the demo workspace doesn't exercise this yet.) |
| **Artifacts** | `ArtifactService.save(path="spaces/<id>/<name>")` — artifacts land in the Space's folder, same namespace rule as memory. |

### 6.3 The Space view

Three regions on one screen:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Launch Q4 campaign                      correlation_id: space:launch-q4 │
│ one place for every Dot I talk to about it                               │
├────────────────┬────────────────────────────────────────────────────────┤
│ ACTIVITY       │ ACTIVE DOT  [Handle Item ▾]   — role: Draft replies +… │
│ ─────────────  ├────────────────────────────────────────────────────────┤
│ handle-item    │                                                        │
│  completed     │   assistant: Which item should I take?                 │
│  08:36 AM      │                                                        │
│                │                                                        │
│ morning-brief  │                                                        │
│  completed     │                                                        │
│  08:36 AM      │                                                        │
│                │   [ textarea composer ]                                │
│ ARTIFACTS      │                                                        │
│ spaces/…/      │                                                        │
│                │                                                        │
│ PENDING        │                                                        │
│ APPROVALS      │                                                        │
└────────────────┴────────────────────────────────────────────────────────┘
```

- **Left rail** polls `/api/spaces/[id]/activity` every 5s and renders the
  jobs sorted newest-first. Artifacts and Pending Approvals rails are
  placeholders until the demo workspaces emit real data.
- **Right pane** is a stock `CopilotChat` whose `HttpAgent` embeds the active
  Dot id and the Space id in the URL (see §6.2).
- **Dot picker** swaps the active Dot without leaving the Space URL. The
  `HttpAgent` is keyed by `(Dot, Space)` so each Dot keeps a stable
  `threadId`, meaning LangGraph's checkpointer resumes within the Dot's
  conversation — _and_ every run still shares the Space's `correlation_id`.

### 6.4 The one active Dot rule

The user picks one Dot at a time in a Space. Not because the UI is lazy, but
because that is the pattern that works:

- A chat UI only has one composer; multiplexing one input across many agents
  is confusing to use and prompts hard conflicts (who writes next? who sees
  what I wrote?). The resolved pattern is a *picker*, not *tabs*.
- Dot-to-Dot talk happens through the `agent` skill (one Dot's topology calls
  another's as a sub-run). That is a tool call inside the Space with the
  shared `correlation_id`, so the activity feed shows it as a nested run —
  not a parallel chat.
- If the user wants true parallel work, they open a second browser tab on the
  same Space URL. Both tabs share the same correlation_id, same activity
  feed, same audit view.

---

## 7. Authoring: how new Dots get into the sidebar

The Author Dot is the primary path. The user chats with it; it calls the
reusable `swarmkit:author:topology` skill (shipped with the runtime), which
asks what the coworker should do, drafts a topology, writes it under
`workspace/topologies/`, and POSTs to `/api/dots` to register the new Dot in
`lib/dots.local.json`.

Nothing in the UI edits YAML. The one-step publish is itself a Dot: Author.

---

## 8. Observability — one id, four views

Pick any Space id — say `space:launch-q4-campaign` — and the following views
all key off the same string:

| View | Query |
| --- | --- |
| Space activity (dots app) | `GET /api/spaces/launch-q4-campaign/activity` |
| Jobs history (portal) | `GET /jobs/history?correlation_id=space:launch-q4-campaign` |
| Audit events (portal) | `GET /audit?correlation_id=space:launch-q4-campaign` |
| Governed memory | namespace `space:launch-q4-campaign` |

That's the whole "observability model for Spaces". There is no new table, no
new endpoint, no new concept — the primitives already filter by
`correlation_id`.

---

## 9. Running the app

### 9.1 Standalone mock (no Python)

```bash
cd reference/apps/dots
pnpm install
export SESSION_SECRET="$(openssl rand -hex 32)"
export DOTS_OWNER_PASSWORD_HASH="$(node scripts/hash-password.mjs 'change-me')"

# terminal 1
pnpm mock-runtime
# terminal 2
SWARMKIT_URL=http://127.0.0.1:4100 pnpm dev
```

Only the Morning Brief Dot works against the mock. Login as `owner` /
`change-me`.

### 9.2 Live stack — real swarmkit serve

```bash
# terminal 1
uv run swarmkit serve examples/dots-demo/workspace  # or any workspace

# terminal 2
cd reference/apps/dots
SWARMKIT_URL=http://127.0.0.1:8000 pnpm dev
```

The workspace needs `authoring: { expose: true }` on its `workspace.yaml` for
the Author Dot to work. See `docs/spaces.md` for the Spaces live-stack recipe
including a Playwright capture script.

---

## 10. Shipped screenshots

The Spaces slice is captured against a real stack in
[`docs/spaces-screenshots/`](spaces-screenshots/):

| File | What it shows |
| --- | --- |
| `01-spaces-empty.png` | Fresh account, no Spaces. Sidebar shows the Spaces section with a `+` link. |
| `02-new-space-form.png` | The new-Space form with id, name, description. |
| `03-space-view-empty.png` | Space view with the correlation id in the header, empty activity rail, Dot picker set to Handle Item, CopilotKit composer ready. |
| `04-space-chat-handle-item.png` | Handle Item run inside the Space. |
| `05-space-chat-morning-brief.png` | Switched the picker to Morning Brief — same URL, same Space, different Dot. |
| `06-space-activity-cross-dot.png` | Activity rail now shows both runs under one Space. |
| `07-portal-jobs-all.png` | SwarmKit portal `/jobs` view — both `handle-item` and `morning-brief` runs appear with pipeline/correlation_id `space:launch-q4-campaign`. The money shot: one id, two Dots, one timeline, and it's the runtime's own view, not something the dots app made up. |
| `08-portal-jobs-filtered.png` | Portal `/jobs` filtered by that same correlation id. |

Older captures of the single-Dot flow sit in
[`docs/screenshots/`](screenshots/).

---

## 11. What this app deliberately does NOT do

- **No runtime extension.** Every primitive — session, Spaces, authoring,
  observability — composes from what the runtime already exposes. If you find
  yourself editing `packages/runtime` to make a dots-app feature work, you
  are probably going down the wrong path.
- **No per-Dot state in the app.** Threads, memory, audit are all server-side
  concerns of `swarmkit serve`. The dots app forgets everything the moment
  the user closes the tab.
- **No prompt engineering in the proxy.** The proxy adds exactly two keys to
  `context` — `topology` and (optionally) `correlation_id`. The role, the
  instructions, the tools all live in the topology YAML.
- **No ad-hoc API paths.** CLI, chat, triggers, A2A all go through the same
  services. The AG-UI route uses `JobService`; the Space activity feed reads
  `JobStore` through the service layer. See
  `docs/notes/service-layer-discipline.md` in the main repo.

If you add a feature, apply the same rules.
