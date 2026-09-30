# DOT App — BRD + design (frontend + backend)

**Status:** design (issue #990, PR 5 of the DOT split).
**Depends on:** [[dot-workspace]] for the workspace this app is the frontend for; [[mcp-oauth]]
for the OAuth flow the app delegates to the runtime; [[per-caller-credential-delegation]] for
the principal-forwarding semantics on API calls.
**Repo location:** `reference/apps/dot/` (peer to `packages/ui/`, not part of it — per
[[dot-workspace]]).

---

# Part 1 — Business requirements

## Purpose

A standalone web application that serves the DOT workspace to its owner. Renders the morning
brief, exposes per-item delegation (buttons + scoped chat), and — from Sprint 3+ — the sandbox
review flow for authored artefacts and the recursive-authoring surface.

**DOT is the owner's daily driver — the SwarmKit portal is the debug/power surface.** A normal
day the owner never opens the portal. They open the portal when DOT itself looks wrong (a run
failed strangely, an audit question needs answering with more depth than DOT surfaces, a raw
YAML edit needs making). That split shapes every design decision below: anything the owner
touches daily belongs in DOT and lives inside DOT's own UI; anything the owner touches monthly
or in an emergency stays in the portal.

Not a general-purpose portal. Not a replacement for `packages/ui/` (which is SwarmKit's own
platform-observability surface). But not a thin shell either — the app owns the user-facing
surface of the DOT workspace end-to-end.

## Users

**One class of user: the workspace owner.** Owner-only for the foreseeable future (per
[[dot-workspace]]'s committed non-goal). Every design trade-off here is measured against the
owner's experience, not against a multi-user or hosted-SaaS shape.

## Scope

In scope for this design note (Sprint 1–5 of the parent plan):

- **Today's morning brief page** (card stack).
- **History strip** (previous 7 briefs).
- **Per-item card** with buttons + right-rail scoped chat surface.
- **Connections page** — Gmail + Google Calendar status, connect / reconnect / disconnect
  **inline** (the browser stays on DOT throughout the OAuth handshake; runtime does the
  token exchange but the user-facing flow feels native to DOT).
- **Settings page** — the user-facing prefs that a normal owner tunes without ever editing
  workspace YAML: morning brief schedule, LLM provider + model, item-cap on the ranker,
  notification preferences (Sprint 2+ when notifications exist), quiet hours if any. Writes
  back through the runtime's workspace-config surface; the app never edits `workspace.yaml`
  directly.
- **Activity page** — a thin proxy of the runtime's audit log, scoped to the owner: recent
  morning-brief runs, recent per-item runs, promote/rollback events (Sprint 2+). "What
  happened this morning?" is answerable without opening the SwarmKit portal.
- **Usage & cost page** — daily and monthly token counts and cost totals, per provider and
  per topology. Answers "am I burning money faster than expected?" without leaving DOT. A
  compact stat tile on `/activity` shows the same top-line numbers at a glance.
- **Drafts drawer** (Sprint 2+).
- **Authoring review flow** (Sprint 3+).
- **Plugin registry view** (Sprint 4+).
- **Login page** + session management.

Out of scope for the BRD (called out separately below or deferred to the portal):

- Push notifications, email digests, webhook triggers to the app (the run trigger is a
  scheduled trigger in the workspace).
- Multi-user account management, user roles, tenancy.
- Deep workspace editing — the archetype YAML, the topology structure, the credential
  definitions. That is a power surface and stays in the SwarmKit portal / on disk.
- Dashboards, analytics, cross-run reporting. The Activity page shows recent runs; the Usage
  page shows token + cost totals. Anything richer (per-model latency histograms, retry rate
  trends, per-tool-call breakdown) lives in the portal's audit tools.
- **Budget alerting** — the Usage page shows numbers; it does not send an alert when the
  owner blows a budget. That's a Sprint 2+ notifications concern with its own design.
- **Billing / invoicing** — DOT reads cost data the runtime already records; it does not
  reconcile against a provider's actual bill or issue invoices.
- **Per-item cost attribution** — cost is broken down per topology (morning-brief vs
  handle-item), not per individual item. Item-level cost is available via the audit trail
  on any given run and stays there.
- Runtime health / fleet / control-plane views. If DOT itself is broken those live in the
  portal.

## Success criteria

The app is done — for a given sprint — when:

- **Sprint 1**: the owner can open the app on their phone from a browser, log in, see today's
  brief, tap through to a per-item card, use a button, use scoped chat, and read the drafted
  reply. All read-only. No dashboard-mediated inference of what happened; the app narrates
  every step it took.
- **Sprint 2**: the same, plus the Drafts drawer shows authored artefacts (from the workspace's
  future-authoring flow) and the owner can trigger a sandbox run.
- **Sprint 3+**: full self-extending loop reviewable in the app.

Non-numerical for a personal tool. A latency budget still applies (below).

## Non-functional requirements

### Responsive and mobile-first

**Explicit hard requirement.** The app must be usable on a phone as its primary interface;
desktop is the secondary case. Every screen is designed at mobile width first (~360 px), then
scaled up.

Concretely:

- **Breakpoints**: 360 (mobile), 768 (tablet), 1024+ (desktop). Nothing between requires a
  distinct layout; components adapt fluidly.
- **Touch-first affordances**: buttons and interactive rows are ≥44 px tall (Apple HIG /
  Material minimum). No hover-only interactions — everything works with tap.
- **No right-rail on mobile**: the scoped chat surface that sits alongside a card on desktop
  becomes a bottom-sheet (or full-screen with a back button) on mobile. Same underlying state,
  different presentation.
- **History strip adapts**: horizontal scroll on desktop; on mobile it collapses to a "Previous
  briefs" chip that opens a bottom sheet with the list.
- **Text stays legible**: 16 px base minimum on mobile; no pinch-to-zoom required to read a
  card. Line length capped at ~72 characters even on wide screens.
- **The whole app renders correctly with no JS in an emergency**. Not a hard requirement — a
  Next.js SSR pass makes this incidental. Called out because it is a real disaster-recovery
  affordance for an owner-only tool.

### Performance

- First Contentful Paint on mobile 4G target: **< 1.5 s** for the today's-brief page (cached
  after first visit).
- The brief itself may take seconds to render because it depends on a runtime call; the app
  shell paints immediately with a skeleton while data streams in.
- No blocking dependency on JS bundles > 200 KB compressed for the initial route.

### Accessibility

- Keyboard-navigable end-to-end. Every button reachable, focus visible.
- Semantic HTML — headings, landmarks, buttons that are `<button>`.
- Colour contrast AA minimum (Tailwind's default palette meets this for the intended tokens).
- Screen-reader labels for icon-only affordances (there should be very few).

### Privacy

The app renders email and calendar content. **Nothing about that content is logged by the app
itself**. All runtime interactions go through the audit log the runtime already keeps. The app's
own logging is limited to request lines (method, path, status, latency) and errors — never the
body of a response that carries inbox contents.

## Constraints

- **Owner-only.** Every affordance the multi-user case would need — role management, sharing,
  invitations — is not built. If DOT grows a public user base, that becomes its own design
  conversation.
- **Read-only until Sprint 6+.** The app must not offer UI affordances for sending, scheduling,
  or mutating — even greyed out. A disabled button that says "Send" trains the user to expect
  behaviour that does not yet exist and is a lie the moment the workspace's read-only stance
  changes to something else.
- **No app-side database.** Every persistent state item lives in the runtime (via its audit log,
  its OAuth token store, its workspace config). The app is stateless per container restart.
  Session cookies are signed with a secret from env; no session store.
- **No hosted deployment path in this design.** The compose file boots the app locally or on a
  personal server. If someone wants to host it, they figure out their own reverse proxy.

---

# Part 2 — Frontend design

## Framework

**Next.js 15+ (App Router).** Rationale:

- Server-rendered by default — the shell paints instantly on mobile without a JS payload gate.
- API routes co-located with pages, so backend and frontend live in one deployable.
- The rest of the SwarmKit ecosystem uses Next.js (`packages/ui/`); consistent tooling means
  the owner's mental model transfers.
- Static-export path is available if a future deployment wants to peel the app off from its API
  routes (e.g. serving the app from a CDN and pointing at a hosted runtime).

## Design system

**Tailwind CSS + shadcn/ui + Radix primitives + Geist font**, matching the SwarmKit portal
(`project_ui_design_system`) so the owner does not learn two visual vocabularies for two apps
they run side by side.

- **Colour tokens**: neutral-dark palette as the SwarmKit portal uses (Zinc/Slate scale), sky
  as the accent (not the default Tailwind blue — per SwarmKit's design-system memory).
- **Component primitives**: Radix under the hood, shadcn/ui as the copy-into-repo layer,
  variants tuned per-app. No component library beyond that.
- **Icons**: Lucide, sized 20 px on desktop, 24 px on mobile.

Anything beyond this catalogue is bespoke and lives under `reference/apps/dot/components/`.

## Screens

### 1. Login

Single form, centred, unauthenticated. Username + password fields; a submit button. Errors
inline under the field they belong to. No branding beyond the app name.

Mobile: full-height flexbox centre. Desktop: same shape, capped at 400 px wide.

Landing here happens on any unauthenticated request; the URL the user was going to is preserved
via a `?next=<path>` query and honoured after login.

### 2. Today's brief

The default authenticated route (`/`). A vertical stack of item cards, one per ranked item in
the latest morning-brief run. Above the stack: a compact header with the date + a "Refresh"
button. Below the stack: the history strip (or its mobile equivalent).

Each card carries:

- **Source badge** — small icon + text: "Gmail" / "Calendar", so the item type reads at a
  glance without decoding text.
- **Title** — the ranker's `title` field. Truncated at two lines with ellipsis on mobile;
  three lines on desktop.
- **Why line** — the ranker's `why` field. Single line, dim tone, italic. Not truncated —
  wraps freely.
- **Buttons row** — up to 5 buttons corresponding to the item's `suggested_action` and one or
  two peer actions. On mobile these become icon-first with labels beneath at ≥44 px height.
- **"Handle" chevron** — tapping the card body (not a button) opens the per-item detail view.

Mobile: single column, cards edge-to-edge with 8 px inset. Desktop: single column, cards capped
at 720 px wide, centred, with a right rail (see per-item detail).

**Empty state**: "No brief yet today. It runs at [scheduled time] — trigger it manually?" with
a button. The button posts to the backend which invokes `morning-brief` synchronously.

### 3. Per-item detail

Opens when the user taps a card body. Not a new route — a **transitional state** on the same
route with the URL updated to `/item/:id`. On desktop the item detail slides in from the right
as a rail; on mobile it takes over the viewport with a back arrow returning to the brief.

Contents:

- The item card, expanded (buttons still visible, plus a description if the source has one).
- **Right rail on desktop / bottom half on mobile — scoped chat**:
  - Message stream (system message identifying the item, then owner/assistant turns).
  - Input at the bottom (multi-line textarea auto-growing to 5 lines max).
  - Send button (`⏎` on desktop, tap on mobile).
- **Action result panel** — when a button was clicked or the chat produced an output, the
  result appears here. Card format matching the artefact type (email draft renders as an
  editable-looking read-only form; prep note renders as a structured list; retrieved
  conversation renders as a thread summary with links).

Mobile layout: the item + buttons are the top pane; chat is the bottom sheet (drawn at 60% of
viewport by default, drag-up to full-screen). Action results appear inline in the item pane,
not the chat pane.

### 4. Connections

`/connections`. Two rows: Gmail, Google Calendar. Each shows:

- Provider name + icon.
- Status: **Connected as `<owner>`** with expiry / **Not connected** / **Expired — reconnect**.
- Action button — Connect, Reconnect, Disconnect.

The OAuth flow feels native to DOT — **the browser stays on DOT throughout**. Mechanism:

1. Owner clicks Connect. DOT's backend calls the runtime's `/oauth/:provider/start` with
   `return_to=<DOT origin>/connections?connected=<provider>`.
2. Runtime returns Google's OAuth URL (Google's redirect URI is on the runtime, unchanged).
3. DOT redirects the browser to Google.
4. Google → runtime callback → token exchange → token stored per-owner → runtime issues a
   302 to the `return_to` URL back on DOT.
5. DOT's `/connections` page reloads with success state.

DOT never sees a Google token; the runtime's OAuth store is unchanged from [[mcp-oauth]].
What changes is only that the OAuth start endpoint learns to accept a `return_to` (a small
runtime addition, tracked as a dependent PR — see "Runtime dependencies" in Part 3).

Mobile: full-width rows stacked. Desktop: same, capped at 720 px.

### 5. Settings

`/settings`. The prefs a normal owner tunes without ever editing YAML. Groups:

- **Brief schedule** — a time picker (defaults to 07:30 local). Writes back to the workspace's
  trigger config through the runtime's workspace-config surface.
- **Model provider** — a dropdown of installed providers (Ollama, OpenRouter, whichever
  cloud providers have credentials wired) + a model name field. Applies to DOT's aggregator,
  ranker, drafter, prepper archetypes. Overrides the `defaults.model` on each; the archetypes
  themselves stay YAML-canonical.
- **Ranker item cap** — a number (default 10, per the workspace's `output_schema`). Passed as
  input to the `morning-brief` topology.
- **Notification preferences** (Sprint 2+ when notifications exist) — email/webhook, quiet
  hours.

Every setting is a small typed field; no rich text, no complex forms. Save on change (debounced)
with an inline "Saved" indicator. Mobile: single column, generous vertical spacing so touch
targets are unambiguous. Desktop: two-column form at 800 px.

**Not in Settings** — anything the SwarmKit portal owns because it's a power/debug affordance:
archetype YAML, topology structure, credential store contents, MCP server registration
beyond the two DOT ships with. If the owner needs those, DOT surfaces a "Advanced (in the
SwarmKit portal)" link at the bottom of the page rather than pretending they don't exist.

### 6. Activity

`/activity`. A thin proxy of the runtime's audit log, scoped to the owner. Lists recent runs:

- Latest morning brief (with a link to view its ranked items).
- Recent per-item runs (draft-reply, prep-meeting, etc.) with the item they were about.
- Sprint 2+: promote / rollback events on authored artefacts.

Each row: run time, topology, one-line result summary, status pill (ok / failed / partial).
Tapping a row opens a detail view showing the per-step audit trail — but only the fields
useful to a daily user (which archetypes ran, elapsed time, any errors). Full detail
(model calls, tokens, policy decisions) is a "See full audit in the portal" link that goes
to the portal's audit view for the run id.

The Activity page answers "what happened this morning?" without opening the portal. Deeper
questions ("why did this fail?") still delegate.

Mobile: single-column list; row detail is a bottom sheet or new full-screen page. Desktop:
list on the left, detail on the right when a row is selected.

### 7. Usage & cost

`/usage`. Answers "am I burning money faster than expected?" without leaving DOT.

Top of the page: **stat tile row** — three tiles, tap-to-drill:
- **Today** — tokens (formatted with SI suffix, e.g. "42.1k") and cost in USD ("$0.084").
- **This month** — same shape, month-to-date.
- **Last 30 days** — rolling window, so month boundaries do not hide a trend.

Below the tiles: a **30-day sparkline / bar chart** — one bar per day, height by cost. Mouse
hover or tap for the day's totals. This is the "am I trending up?" glance.

Below that: two **breakdowns** for the current month, each as a small ranked list:
- **By provider / model** — e.g. `openrouter/kimi-k2.5`: 480k tokens, $2.14 / `ollama/qwen2.5`:
  1.2M tokens, $0.00.
- **By topology** — `morning-brief`: 320k tokens, $0.94 / `handle-item`: 640k tokens, $1.20.

Bottom of the page: **"See individual runs in the Activity page"** link — Usage is aggregate;
per-run detail is Activity's job.

Mobile: single column, stat tiles wrap to 2×2, chart is full-width, breakdowns stack. Desktop:
tiles side-by-side, chart full-width capped at 960 px, breakdowns two-column.

Also: **a compact stat tile lives at the top of `/activity`** showing today's tokens + cost,
so the number is visible in the "what happened" flow without requiring a nav.

**Not on this page** — budget alerting (Sprint 2+ notifications concern), reconciliation
against the provider's actual bill (out of scope), per-item cost attribution (available in
Activity's per-run detail, not on Usage).

### 8. Drafts (Sprint 2+)

`/drafts`. List of authored-but-not-promoted artefacts (archetypes, topologies, plugin
manifests). Each row: title, `authored at`, source topology, status. Tapping opens the artefact
in a preview + "Sandbox" / "Promote" / "Refine" / "Discard" affordances.

Not sketched in detail in this design note — Sprint 2 gets its own sub-note.

### 9. Plugins (Sprint 4+)

`/plugins`. List of active DOT plugin manifests. Tapping opens the manifest's YAML in a
read-only viewer (owner edits by authoring, not by direct edit). "Disable" affordance flips
the manifest's `status` back to `draft`.

Not sketched in detail — Sprint 4 gets its own sub-note.

## Component architecture

Flat and small on purpose. No global state library.

```
reference/apps/dot/
  app/
    layout.tsx                # HTML shell, session bootstrap
    page.tsx                  # today's brief
    item/[id]/page.tsx        # per-item detail (uses layout intercept for desktop rail)
    connections/page.tsx
    drafts/page.tsx           # Sprint 2+
    plugins/page.tsx          # Sprint 4+
    login/page.tsx
    api/
      auth/[...auth]/route.ts # login, logout, session check
      brief/route.ts          # GET latest brief
      brief/run/route.ts      # POST trigger a morning brief
      items/[id]/handle/route.ts # POST invoke handle-item topology
      items/[id]/chat/route.ts   # POST scoped chat, streams response
      connections/route.ts    # GET status, GET redirect URL for connect flow
  components/
    item-card.tsx             # the card in both list + detail contexts
    action-buttons.tsx        # dispatches to backend endpoints
    chat-pane.tsx             # scoped chat surface
    result-panel.tsx          # renders artefact by kind
    connection-row.tsx
    history-strip.tsx
    ui/                       # shadcn/ui primitives
  lib/
    swarmkit-client.ts        # thin HTTP client for the SwarmKit runtime
    session.ts                # cookie sign/verify helpers
    types.ts                  # shared TS types (Item, Brief, Draft, PluginManifest)
```

## Responsive strategy

**Mobile-first Tailwind.** Every utility class starts at the mobile size; larger breakpoints
override. Layout adapts through:

- Container width caps (`max-w-screen-md` on desktop, edge-to-edge on mobile).
- Flexbox rearrangement (`flex-col md:flex-row`).
- Right-rail vs bottom-sheet: a `<Dialog>`-shaped component with different position variants
  triggered by a `matchMedia` hook.
- Font sizing: `text-base md:text-sm` — mobile stays at 16 px minimum to prevent iOS Safari
  zoom-on-focus; desktop tightens.

## State management

- **Server state**: React Query (`@tanstack/react-query`) for HTTP fetches with cache +
  automatic refetch on window focus.
- **Client state**: React state / context. No Redux, no Zustand, no MobX.
- **URL as state**: the current item id, the current draft id, filter selections — all
  reflected in the URL so back/forward works and links are shareable.

Real-time updates: the brief render polls every 30 s while the tab is visible; a running
handle-item invocation streams via Server-Sent Events (Next.js API route yields chunks).
Neither uses WebSockets — nothing about DOT's traffic pattern needs one.

## Accessibility, called out

- Every button carries an accessible name (`aria-label` when icon-only).
- The chat pane's message list has `role="log"` with `aria-live="polite"` so screen readers
  hear new assistant messages without stealing focus.
- The bottom-sheet on mobile traps focus while open and returns it on close (Radix's Dialog
  primitive handles this).
- All colour uses (status badges, error text) has a non-colour cue (icon, text) alongside.

---

# Part 3 — Backend design

## Framework

**Next.js API routes** (Node runtime, App Router). Backend and frontend live in the same
deployable — the `just dot-up` command starts one Next.js server, and it serves both.

Rationale:

- Zero extra runtime cost — the Next.js server is already required for SSR; adding routes to
  it is free.
- Simpler deployment story — one container, one port, one process to supervise.
- Session cookies work naturally same-origin without CORS gymnastics.

## Endpoint list

Every endpoint is JSON in / JSON out unless noted.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/login` | Username + password → session cookie. |
| POST | `/api/auth/logout` | Clears session cookie. |
| GET | `/api/auth/whoami` | Returns `{ owner, connected: { gmail: bool, calendar: bool } }` or 401. |
| GET | `/api/brief` | Latest morning-brief run's ranked items for the owner. |
| POST | `/api/brief/run` | Ad-hoc trigger of the `morning-brief` topology; SSE stream of progress + final result. |
| POST | `/api/items/:id/handle` | Invoke `handle-item` topology; SSE stream. Body: `{ suggested_action, user_intent? }`. |
| POST | `/api/items/:id/chat` | Same as handle, but with `user_intent` as the sole payload — the scoped-chat variant. |
| GET | `/api/connections` | Provider status for Gmail + Calendar. |
| POST | `/api/connections/:provider/connect` | Kicks off the OAuth flow. Backend calls runtime `/oauth/:provider/start` with `return_to=<DOT>/connections?connected=:provider`; returns the Google URL for the browser to redirect to. |
| POST | `/api/connections/:provider/disconnect` | Delegates to the runtime's disconnect endpoint. |
| GET | `/api/settings` | Current user-facing prefs, projected from the workspace config the runtime holds. |
| PATCH | `/api/settings` | Apply a partial settings update; DOT translates to a workspace-config patch and forwards. |
| GET | `/api/activity` | Recent runs for the owner, scoped to DOT's topologies (`morning-brief`, `handle-item`). Optional `?cursor=` for paging. |
| GET | `/api/activity/:runId` | Detail for a single run — the user-facing shape (which archetypes, elapsed, errors), not the full audit. |
| GET | `/api/usage/summary` | Today / month-to-date / last-30-days aggregates: tokens_in, tokens_out, cost_usd. |
| GET | `/api/usage/daily?window=30` | Per-day totals for the sparkline / bar chart. |
| GET | `/api/usage/breakdown?by=provider\|topology&window=month` | Ranked list of per-provider or per-topology totals for the given window. |
| GET | `/api/drafts` | Sprint 2+. |
| GET | `/api/plugins` | Sprint 4+. |

Every endpoint that touches the runtime enforces the owner session first; unauthenticated
callers get a 401 with `WWW-Authenticate: Login realm="dot"`, and the browser is redirected
to `/login`.

## Auth model

**Two distinct auth surfaces, deliberately kept separate:**

### 1. DOT's own session (owner ↔ DOT app)

- The owner logs in to DOT with a username + password configured via env
  (`DOT_OWNER_USERNAME`, `DOT_OWNER_PASSWORD_HASH` — the hash, not the plaintext; produced with
  `openssl passwd -6` at install time and stored in `.env`).
- Successful login sets a signed cookie (`dot_session`) with the owner's identity + issue time.
  Signed with `SESSION_SECRET` (per [[dot-workspace]]'s parent design note — the same
  discipline as the fleet panel's `SWARMKIT_CONTROL_PLANE_SECRET_KEY`).
- Cookie is `HttpOnly`, `SameSite=Lax`, `Secure` when the request came over HTTPS, `Max-Age`
  30 days. Rotates on login (no fixation) and invalidates on logout server-side (a
  short-window blocklist in-memory — since there is one owner, the blocklist is one string).
- **Not OAuth for the DOT login itself.** Username + password is honest for a single-owner
  personal tool; adding an OAuth dance to log in to a personal app you deployed yourself is
  ceremony without benefit.

### 2. Runtime authentication (DOT ↔ SwarmKit runtime)

- Every request DOT's backend makes to the runtime carries an **owner-scoped bearer token**.
- The token is issued by the runtime's `AuthProvider` (existing surface in
  `packages/runtime/src/swarmkit_runtime/auth/_provider.py`). DOT registers itself as a
  known client at install time via a runtime CLI command (proposed:
  `swarmkit auth issue-client-token dot --owner=<email> --lifetime=90d`); the resulting
  token is stored in DOT's `.env` as `SWARMKIT_RUNTIME_TOKEN`.
- The token identifies the caller as the owner's `client_id` — which is what
  `packages/runtime/src/swarmkit_runtime/_principal.py` looks up when a per-user credential
  needs to resolve to a Google token.
- Rotating this token is a single command; DOT does not manage its own OAuth against the
  runtime.

**Why not delegate DOT login through the runtime's OAuth too?** Because DOT is a separate
application with a separate trust boundary. The runtime's session cookie is tied to a
Next.js origin (`packages/ui/`); DOT is a different origin. Sharing the cookie would require
either same-origin deployment (which the parent design note explicitly does not want) or a
cross-origin cookie ceremony (which is a fragile dance for a personal tool). A separate
session with an owner-scoped bearer to the runtime is simpler and honest.

**What Gmail/Calendar OAuth is:** entirely handled by the runtime, unchanged. DOT redirects
the owner's browser through the runtime's OAuth start (with `return_to` back to DOT so the
UX feels native — see the Connections screen sketch); the runtime stores the encrypted
token per owner in its `oauth_tokens` table; every subsequent tool call resolves the token
from that single store. DOT never sees a Google token, on either lane.

**No parallel connection UI, no parallel token store.** The Connections page in DOT is a
proxy over the runtime's existing connections surface; the fast-lane endpoint invokes
tools with credentials the runtime resolves; the freeform-lane LLM calls go through the
same credential path as any other topology-borne tool call. The whole point of the two-
lane design is that the *credential authority* stays exactly where [[mcp-oauth]] put it.

## Session management

- Cookie encoding: signed with HMAC-SHA256 using `SESSION_SECRET`. Payload is JSON: `{ owner,
  iat, exp, jti }`.
- Blocklist: in-memory `Set<string>` of revoked `jti`s from logout events, expiring at cookie
  natural expiry. Owner-only means the set contains at most one entry at any point; a full
  in-memory store is honest.
- Refresh: on any authenticated request within 7 days of expiry, a new cookie is issued
  transparently. Owner never sees a spontaneous re-login unless they're gone > 30 days.

## Runtime integration

## Two data-fetch lanes, one credential authority

Data pulls happen through one of two lanes, both routed through the runtime so credentials
live in exactly one place:

- **Fast lane (deterministic, used by the scheduled / user-tapped-refresh path).** DOT's
  backend calls a runtime endpoint that invokes a specific MCP tool on the owner's behalf
  with fixed arguments (`search_threads` with `is:unread in:inbox` for the last 24 h,
  `list_events` with today+tomorrow window). Runtime resolves the owner's credential from
  its OAuth store and forwards to the MCP server. Result comes back structured. Zero LLM
  tokens on data fetching.

- **Freeform lane (LLM-mediated, used by scoped chat, dot-authoring, downstream authored
  topologies, and any CLI/schedule invocation).** An archetype's LLM has MCP tools available
  and decides which to call. Same runtime, same MCP servers, same credentials — the LLM
  drives the tool selection instead of DOT's app code.

**The credential invariant is that both lanes resolve tokens through exactly the same
runtime path** (`oauth_tokens` table, per-user, per [[mcp-oauth]]). DOT never holds a Google
token. There is no parallel connection UI, no parallel token store, no rewrite of the
connections work. The Connections page in DOT delegates to the runtime's existing OAuth
start (with a `return_to` back to DOT) — same flow the portal uses.

DOT's backend uses a thin `SwarmKitClient` (`lib/swarmkit-client.ts`) with the methods DOT needs:

- `getRun(topologyId, runId?)` — fetch the most recent run's audit-log-committed output for
  the given topology + owner. Wraps `GET /api/runs?topology=&owner=&latest=true`.
- `startRun(topologyId, input, principal)` — POST to `/api/runs` with an SSE response, forwards
  chunks to the caller.
- `listRuns(owner, topologies?, cursor?)` — for the Activity page. Wraps `GET /audit` with
  filters.
- `getUsageAggregate(owner, window, groupBy?)` — for the Usage page. Wraps whichever runtime
  aggregation endpoint ships; if none does, DOT falls back to paging `/audit` and aggregating
  in-memory (fine for owner-only volumes, wrong shape at scale — but the whole design is
  owner-only, so scale is not the constraint).
- `getMyCredentials()` — GET `/api/oauth/my-credentials` (from #982).
- `startOAuth(provider, returnTo)` — POST runtime's `/oauth/:provider/start` with a return
  URL, receives Google's auth URL back.
- `disconnectOAuth(provider)` — DELETE runtime's `/api/oauth/credentials/:id`.
- `getWorkspaceConfig()` / `patchWorkspaceConfig(patch)` — for Settings. Wraps the runtime's
  workspace config surface.
- `invokeMcpTool(serverId, tool, args)` — for the fast lane. POSTs to a runtime endpoint that
  resolves the owner's credential from the OAuth store, invokes the named MCP tool via the
  runtime's own MCP client machinery, records the call in the audit log, and returns the
  structured result. **DOT holds no MCP transport and no Google token**; the runtime does
  every part of that work exactly as it does for an LLM-driven tool call in a topology.

Every call carries the runtime bearer token as `Authorization: Bearer <SWARMKIT_RUNTIME_TOKEN>`
and the owner's identity as an `X-Owner` header (the runtime's `AuthProvider` picks this up
into `current_principal`, letting per-user credentials resolve to the right Google token).

## Runtime dependencies

DOT depends on runtime surfaces that partially exist and partially need to be added. The
audit surface is confirmed (`/audit?limit=` returns per-run entries; seen live during the
Minder rehearsal). The OAuth start endpoint exists per [[mcp-oauth]] but I do not know
whether it accepts a `return_to` parameter today — if not, that is a small runtime PR
tracked as a Sprint 1 dependency of PR 5. Same for workspace-config PATCH: `packages/ui/`
already writes workspace edits from the portal, so the endpoint exists; whether the shape
DOT's Settings page needs (a JSON-patch-like partial update) matches what ships is worth
verifying before PR 5's Settings commit lands.

Explicitly tracked as dependent PRs (each small, each landable independently):

- **Runtime PR — `return_to` on OAuth start.** If the shipped endpoint does not already
  accept + honour a `return_to` parameter, add it. Trivial change (2–3 lines + a signed-URL
  check so the parameter cannot be used as an open-redirect vector).
- **Runtime PR — `swarmkit auth issue-client-token`.** Already tracked as Q2 in Part 7. If a
  shipped equivalent exists (a persistent API token surface), point at that instead.
- **Runtime PR — Owner-scoped `/audit?owner=<>` filter.** Confirm the audit endpoint accepts
  an owner filter today. If not, small runtime addition.
- **Runtime PR — Usage aggregation endpoint.** Optional: a `/audit/aggregate` that groups by
  day / provider / topology server-side. If it doesn't ship, DOT does the aggregation client-
  side (per `getUsageAggregate` above). Worth having eventually; not blocking for MVP.
- **Runtime PR — MCP tool invocation on behalf of an owner (fast-lane endpoint).** DOT's
  fast lane needs to call an MCP tool by name with the owner's credential resolved by the
  runtime. Whether the runtime ships this today (either as an equivalent of `swarmkit
  mcp-serve`'s tool-forwarding semantics or as a dedicated REST route like
  `POST /api/mcp/:serverId/invoke`) needs verification. If not: small addition that reuses
  the same credential-resolution + audit-recording machinery topologies already exercise
  when an LLM calls a tool. **Critical property: this endpoint must NOT return the
  resolved token to DOT** — it invokes the tool server-side and returns only the tool's
  result. DOT holding a Google token would violate the single-place-for-credentials
  constraint that this whole design turns on.

None of these five block PR 5 from starting — the scaffold + login + brief commits can
land against the runtime as it is. Settings, Connections, Activity, and Usage wait on
whichever runtime additions are needed, and the fast-lane commit waits on the tool-
invocation endpoint (or its equivalent already shipping).

## Env config

Every variable required at startup. No silent defaults for anything security-relevant.

| Var | Required | Description |
|---|---|---|
| `SWARMKIT_URL` | yes | Runtime base URL. |
| `SWARMKIT_RUNTIME_TOKEN` | yes | Owner-scoped bearer to the runtime. |
| `SESSION_SECRET` | yes | HMAC-SHA256 key for signing session cookies (`openssl rand -hex 32`). |
| `DOT_OWNER_USERNAME` | yes | Owner's login username. |
| `DOT_OWNER_PASSWORD_HASH` | yes | scrypt hash of owner's login password. |
| `DOT_OWNER_IDENTITY` | yes | Owner's SwarmKit `client_id` (email or similar). |
| `PORT` | no (default 3400) | HTTP port. |
| `NODE_ENV` | no (default production) | Standard. |

`.env.example` in the repo lists every variable with a comment; missing anything required fails
the container start with a message naming the variable.

---

# Part 4 — Deployment

## Dockerfile shape

Multi-stage, matching the parent design note's ~150 MB target:

```
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

FROM node:20-alpine AS build
WORKDIR /app
COPY . .
COPY --from=deps /app/node_modules ./node_modules
RUN pnpm build

FROM node:20-alpine AS run
WORKDIR /app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
ENV PORT=3400 NODE_ENV=production
EXPOSE 3400
CMD ["node", "server.js"]
```

Next.js `output: 'standalone'` in `next.config.js` produces the minimal Node bundle.

## docker-compose.yml

Two profiles per the parent design:

```yaml
services:
  dot:
    image: ghcr.io/delivstat/dot-app:latest
    build: .   # so the local file can `up --build` without the image
    ports:
      - "3400:3400"
    env_file: .env
    restart: unless-stopped
    depends_on:
      swarmkit: { condition: service_started, required: false }

  swarmkit:
    profiles: [all-in-one]
    image: ghcr.io/delivstat/swarmkit-runtime:latest
    ports:
      - "8321:8321"
    volumes:
      - ./data:/data
    restart: unless-stopped

  # `default` profile: only `dot` starts; SWARMKIT_URL in .env points at an external runtime.
  # `all-in-one` profile: `dot + swarmkit` start together on the same host.
```

## just dot-up

```just
dot-up:
    docker compose -f reference/apps/dot/docker-compose.yml up -d

dot-up-all:
    docker compose -f reference/apps/dot/docker-compose.yml --profile all-in-one up -d

dot-down:
    docker compose -f reference/apps/dot/docker-compose.yml down

dot-logs:
    docker compose -f reference/apps/dot/docker-compose.yml logs -f dot
```

## Published image

`ghcr.io/delivstat/dot-app`, tagged per release. GHA workflow at
`.github/workflows/dot-image.yml` builds + pushes on tag `dot-v*`.

---

# Part 5 — Test plan

## Frontend

- **Component tests** (Vitest + Testing Library) — every interactive component has a test:
  `ItemCard` renders each `suggested_action` correctly, `ActionButtons` fires the right
  endpoint, `ChatPane` streams messages, `ConnectionRow` handles each of the 3 statuses.
- **Responsive tests** (Playwright) — a test that opens each page at 360, 768, 1024 widths
  and asserts key elements are visible (no overflow, no cut-off text). Screenshots recorded
  per breakpoint on every PR touching UI.
- **Accessibility tests** (axe-core) — run against each page in CI, fails on serious/critical
  violations.

## Backend

- **Handler unit tests** (Vitest) — every API route with mocked `SwarmKitClient` and
  `session.ts` helpers. Auth-required endpoints refuse without a valid cookie; owner-required
  endpoints refuse a valid cookie for a different owner.
- **Cookie tests** — sign/verify round-trip; a cookie signed with a different secret is
  rejected; an expired cookie is rejected; a revoked `jti` is rejected.
- **Integration test** (Playwright) — end-to-end against a fake `SwarmKitClient` that returns
  canned responses. Login → today's brief → tap item → click button → assert result panel
  renders. Same test at three widths.

## Container

- **Image smoke test** — build the image, boot it against a mock runtime that returns 200 on
  `/health`, curl `/api/auth/whoami` and expect a 401 (unauthenticated).
- **Compose smoke test** — `docker compose up -d` in the default profile against a mock
  runtime; the container must be healthy in < 30 s.

---

# Part 6 — Commit split within PR 5

PR 5 is materially bigger than PR 2–4. Splitting into a sequence of commits on one branch,
each independently reviewable:

1. **Scaffold** — `next.config.js`, `package.json`, Tailwind + shadcn init, empty routes for
   every page, README.md placeholder. Verify: `pnpm dev` boots to a blank shell.
2. **Auth** — `/login` page, `/api/auth/*` routes, `session.ts` helpers, `SwarmKitClient`
   scaffold with the runtime-token forwarding. Verify: login round-trip against a mock
   runtime.
3. **Today's brief (static)** — `page.tsx` with a hardcoded fixture, `ItemCard`,
   `HistoryStrip`, mobile-first layout. Verify: renders correctly at 360/768/1024.
4. **Today's brief (live)** — `/api/brief`, React Query wiring, `/api/brief/run` for the
   ad-hoc trigger. Verify: end-to-end against a mock runtime returning a canned brief.
5. **Per-item detail** — `/item/[id]/page.tsx`, `ActionButtons`, `ChatPane`, `ResultPanel`,
   `/api/items/[id]/handle` + `/chat`. Verify: end-to-end for each `suggested_action`.
6. **Connections** — `/connections/page.tsx`, `/api/connections/*` routes including the
   inline OAuth flow (with `return_to` back to DOT). Verify: full connect / disconnect
   round-trip against a mock runtime that fakes Google's consent.
7. **Settings** — `/settings/page.tsx`, `/api/settings` GET + PATCH, workspace-config
   translation in `SwarmKitClient`. Verify: change schedule + LLM provider, refresh, values
   persist through the runtime.
8. **Activity** — `/activity/page.tsx`, `/api/activity` list + detail, audit-log projection
   into the user-facing shape. Verify: list renders, detail expands, "See full audit in the
   portal" link is present.
9. **Usage & cost** — `/usage/page.tsx`, `/api/usage/*` routes, stat tiles + 30-day chart +
   provider/topology breakdowns, plus the compact tile at the top of `/activity`. Follows
   the dataviz skill for chart colour + shape. Verify: tiles match the raw audit numbers,
   sparkline renders correctly at 360/768/1024, empty-state message when no runs yet.
10. **Dockerfile + compose + `just dot-up`** — build + smoke tests + GHA image workflow.
11. **README + `docs/dot-quickstart.md`** — how to install, connect accounts, run the app.

Each commit ships with the tests it enables.

---

# Part 7 — Open questions

Every question has a proposed default so implementation can start on those defaults if you
say "go."

- **Q1 — Password storage.** Options: (a) scrypt hash in env (`DOT_OWNER_PASSWORD_HASH`) as
  proposed above; (b) plaintext env for personal-use simplicity; (c) a small SQLite auth store
  the app maintains. *Default: (a) — scrypt hash in env. Simple, well-understood, no runtime
  DB dependency.*
- **Q2 — Runtime token issuance.** Proposed a new `swarmkit auth issue-client-token` CLI
  command; this needs to actually exist on the runtime side. Is there a shipped alternative
  (a persistent API token surface) I should point at instead? *Default: propose the CLI
  command as a small dependent PR against the runtime; if it lands during Sprint 1, use it;
  otherwise DOT ships with a documented manual token-mint step.*
- **Q3 — SSE vs long-polling for streaming runs.** Both work; SSE is simpler and better-
  supported on mobile Safari. *Default: SSE.*
- **Q4 — Local dev without running the runtime.** Options: (a) require a running runtime for
  any development (fastest to ship, brittle for offline work); (b) ship a mock-runtime harness
  the app can point at (more work now, better DX later). *Default: (b) — small mock runtime
  under `mocks/` that responds to the 3 client methods with fixtures. Also drives Playwright.*
- **Q5 — First-run bootstrap.** Options: (a) fail startup if env is incomplete; (b) run a
  one-time interactive `dot-init` command that writes `.env`; (c) both — startup fails, and
  `dot-init` is the recovery. *Default: (c). Startup errors name every missing variable;
  `dot-init` produces a good `.env` on demand.*

---

# What lands as PR 5

Everything in Part 6 (steps 1–8). Nothing from Sprints 2–5 (Drafts, Plugins, sandbox
lifecycle, authoring surfaces) — those are separate design conversations at their own
sprint boundaries.
