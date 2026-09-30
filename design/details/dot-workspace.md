# DOT — the personal-workflow reference workspace

**Status:** design (issue #990).
**Design references:** [[mcp-oauth]] for the per-user OAuth binding; §8.5 (`GovernanceProvider`),
§8.7 (structural approval scopes) for the HITL gates. DOT is Dominique Rose Van-Winther's
Daily Optimization Tracker, described in
[*How Dominique Rebuilt Her Morning Around AI*](https://www.linkedin.com/pulse/how-dominique-rebuilt-her-morning-around-ai-chaithra-madan-qpmbc/)
by Chaithra Madan — the mechanism there is generic; what this note designs is a SwarmKit
implementation of it as a shipped reference.

## Goal

A reference workspace + companion application, primarily for the workspace owner's own use, that:

- Pulls the owner's morning context (inbox, calendar, recent conversations).
- Surfaces a ranked list of actionable items.
- Delegates each item to a small archetype (draft, retrieve, summarise, propose) with human
  approval before any outbound effect.
- **Extends itself.** Both DOT's SwarmKit workspace (new archetypes / topologies / MCP
  integrations) and DOT's own UI (new per-item plugins) can be authored *from within the app*,
  through SwarmKit's shipped authoring machinery — see "Self-extending" below.

Three things at once:

1. A genuinely useful personal-productivity tool for the owner.
2. The flagship demonstration of SwarmKit's per-user OAuth surface (#976, #982) — currently
   shipped, currently unshowcased.
3. **A living proof of SwarmKit's third pillar** — "swarms grow through human-approved
   authoring" — applied recursively: DOT grows its own capabilities by authoring, and grows the
   app itself by authoring.

The mechanism is the point. If everything a DOT does — including extending itself — can be
expressed as topology + archetypes + credentials + HITL gates + workspace-scoped plugin
manifests, then SwarmKit is the right platform for a whole class of personal workflow tools, and
DOT is the first.

## Non-goals

- **Not an email client, calendar app, or virtual assistant.** DOT sits alongside those, reading
  them; it does not replace them.
- **Not a monolithic vertical product.** Products built on top of SwarmKit (the runtime as a
  library, a custom domain UI on top) live in their own repos and ship as products. DOT is
  distributed as a reference workspace + supporting MCP bundles, in-tree, next to
  `code-review.yaml` and `knowledge-curator.yaml`. The optional first-party app in front of it
  (see below) is scoped to what the reference needs to be *usable*, not to compete with any
  domain-specific product.
- **Not a Gmail / Google Calendar reimplementation.** The MCP bundles curate existing servers
  (Google's own remote MCP servers by default, community options as fallback) and add manifests +
  liveness probes. Zero API-wrapper code lives in swarmkit-skills.
- **Not send-first.** Sprint 1 is deliberately read-only. Read-write comes later in the sprint
  plan below.
- **Not a general-purpose product.** Owner-first, and owner-only for the foreseeable future. If
  DOT ever grows a wider user base that changes the calculus (governance for shared plugins,
  migration for user-authored manifests, safety review for authored artefacts), that becomes its
  own design conversation. The current design is deliberately not paying for those affordances
  yet.

## Shape

Three delivered pieces, each with its own section below: two MCP bundles (curated, not
implemented), the reference workspace, and a standalone companion app.

### 1. Two OAuth-authenticated MCP bundles (in `swarmkit-skills`)

**We do not build the MCP servers. We bundle existing ones.** The MCP ecosystem in 2026 has
mature first-party and community servers for both surfaces; writing our own would waste effort
and put swarmkit-skills in the business of maintaining Gmail/Calendar API compatibility, which is
not a business it wants to be in.

- **Google's own remote MCP servers (developer preview, August 2026):**
  - Gmail: `gmailmcp.googleapis.com` — [Google's docs](https://docs.cloud.google.com/mcp/authenticate-mcp)
  - Calendar: the Google Workspace remote MCP suite covers Gmail, Calendar, Drive, Docs, Sheets,
    Slides, Chat — [configure guide](https://developers.google.com/workspace/calendar/api/guides/configure-mcp-server)
  - Both implement the MCP authorization spec revision **2026-07-28**, which aligns with OAuth 2 +
    OpenID Connect and — importantly — is the same spec [[mcp-oauth]] targets in the runtime's
    OAuth store. First-party, no adapter needed.
  - **Read-only caveat**: as of the August 2026 preview, Google's Gmail MCP cannot send. That
    matches Sprint 1's read-only stance exactly; when send arrives in the sprint plan we either
    wait for Google to lift the preview restriction or add a send-only community bundle then.

- **Community options if we prefer self-hosted for MVP:**
  - Gmail: [GongRzhe/Gmail-MCP-Server](https://github.com/GongRzhe/Gmail-MCP-Server) (auto-auth,
    Claude Desktop-shaped) or [jasonsum/gmail-mcp-server](https://github.com/jasonsum/gmail-mcp-server)
    (Python, cleaner Python integration).
  - Calendar: [nspady/google-calendar-mcp](https://github.com/nspady/google-calendar-mcp) is the
    most-referenced community server; [j3k0/mcp-google-workspace](https://github.com/j3k0/mcp-google-workspace)
    bundles Gmail + Calendar in one server (could halve our surface).

- **What lands in swarmkit-skills, then, is TWO bundles that are curation + probe + manifest**,
  not implementations:
  - `packs/gmail/pack.yaml` — declares which upstream MCP server to point at (Google's remote by
    default; community fallback documented), the OAuth flow, the small subset of tools we
    actually consume from the workspace (`list_recent`, `get_message`, `search`), and a nightly
    liveness probe (per `feedback_skills_catalogue_curation` — probe don't transcribe).
  - `packs/google-calendar/pack.yaml` — same shape, pointing at the Calendar server.

The workspace consumes each through `source: oauth` with `identity: per-user` per [[mcp-oauth]];
a run requires a per-user connection through the portal before it can call either.

**Decision to record in the bundle** (belongs in the manifest's rationale, not this design note):
default to Google's official server when both exist and both work, with a documented fallback to
the community option for anyone who wants to self-host or needs a capability the preview server
does not have yet.

### 2. The `dot` reference workspace (in `reference/workspaces/dot/`)

Layout, mirroring how other reference workspaces are shaped:

```
reference/workspaces/dot/
  workspace.yaml               # credentials, mcp_servers, provider settings
  archetypes/
    context-aggregator.yaml    # pulls last-24h context from Gmail + Calendar
    item-ranker.yaml           # decision skill: rank + structured output
    email-drafter.yaml         # draft in the user's tone (delegated per item)
    meeting-prepper.yaml       # prep notes for an upcoming meeting (delegated)
    conversation-retriever.yaml # find and summarise a past conversation
  topologies/
    morning-brief.yaml         # the top-level swarm: aggregate -> rank -> present
    handle-item.yaml           # invoked per item after human picks it
```

Additional per-item archetypes are added as new item types earn them.

The morning-brief topology has a `supervisor-leader` root, a `context-aggregator` worker (calls
Gmail + Calendar MCPs), and an `item-ranker` worker whose output is a structured list of ≤10
items. The output is committed to the audit trail and surfaced to the human via the app
(section 3 below); no autonomous action is taken.

The handle-item topology is invoked when the human picks an item. It takes the item id + the
aggregator's context and dispatches to the right per-item archetype (draft an email, prep a
meeting, retrieve a past conversation). Each per-item action goes through a HITL gate before any
outbound effect.

### 3. Frontend — a standalone companion app

**In read-only Sprint 1, without a UI, the artifacts already exist.** A morning-brief run
produces:

- A ranked list of ≤10 items with source references (message ids, event ids, thread ids).
- A structured JSON output committed to the audit log.
- Per-item context bundles the handle-item topology can pick up.

That is enough to be inspectable in the portal's existing runs view. It is not enough to be
*useful* — nobody starts their morning by reading a JSON blob in a run tree.

**But DOT is a product, not a SwarmKit feature.** Baking a `/dot` panel into `packages/ui/` would
mix concerns SwarmKit's own architecture avoids: the portal is for platform observability (runs,
audit, credentials, workspace config); a personal-productivity morning ritual is a different
domain and deserves its own container. There is no plugin/extension mechanism for portal panels
today, and building one just for DOT is bad shape — the runtime already exposes an HTTP API that
any frontend can talk to, which is the same seam Minder-style products use.

So the frontend is a **separate application**. Four decisions decide the rest.

**Decision 3.1 — Where does the app's source live?**

- **Option A — `reference/apps/dot/` in this repo, as a peer to `packages/ui/`.** Built with
  `pnpm` as its own package, deployed separately from `swarmkit serve`. Discovered next to the
  reference workspace it's the frontend for. Same repo means CI can smoke-test the two together;
  no new repo to maintain; establishes a pattern (reference *apps*, plural) other reference
  workspaces can adopt when they want their own UI. **This is the first "reference app" —
  worth naming as a pattern.**
- **Option B — `apps/dot/` in swarmkit-skills.** Would live with the two MCP bundles, keeping
  everything DOT-shaped together. Awkward fit though: swarmkit-skills is a *catalogue* of skills
  and MCP wrappers, not an app registry, and adding front-end apps expands its scope in a way its
  current curation-and-probe discipline does not cover.
- **Option C — a new `dot-app` repo.** Full independence, own release cadence. Adds a repo, adds
  a deploy story, adds cross-repo API-drift risk against SwarmKit's runtime. Worth doing when DOT
  is a shipped product with users; overkill for the first reference.

**Recommendation: A.** `reference/apps/dot/` — a peer package in this repo, built separately from
the portal, deployed separately from the runtime, discoverable next to the workspace it belongs
to. This is a new pattern (SwarmKit does not have a reference-app tree today) and it should be
introduced with a short paragraph in `README.md` naming what the pattern is and when a workspace
should have one.

**Decision 3.2 — How does the app get deployed?**

Same pattern every long-lived service in the surrounding ecosystem uses — **hosted Docker image
+ Dockerfile in the source tree + docker-compose.yml that composes it**. A `.next/` static
export was under-specified: even the read-only DOT needs a server process for OAuth session
cookies and API-proxy hygiene, and the operational story of "which port, restarts on crash,
easy update path" wants Docker whether or not the app itself is trivial.

Concrete shape:

- `reference/apps/dot/Dockerfile` — Node runtime, `pnpm build`, `pnpm start` at container start.
  Multi-stage build to keep the shipped image ~150 MB rather than dragging node_modules.
- `reference/apps/dot/docker-compose.yml` — the DOT app as one service. Two profiles:
  - **default** — DOT alone, pointing at an external `SWARMKIT_URL` (someone else's runtime, or
    another compose stack).
  - **`all-in-one`** — DOT + SwarmKit runtime side-by-side in the same stack, for the
    single-owner case where both live on the same box.
- Published image: `ghcr.io/delivstat/dot-app` (matching the org's existing package publishing
  pattern), tagged per release, immutable.
- Env the container reads:
  - `SWARMKIT_URL` — required, no default (fail loudly at start if unset; a silent default is
    worse than a startup error).
  - `SESSION_SECRET` — required, no default, generated with `openssl rand -hex 32` at install.
    Same discipline as the fleet panel's `SWARMKIT_CONTROL_PLANE_SECRET_KEY` (per
    `feedback_fleet_panel_secret_key`); an ephemeral secret on every restart invalidates every
    open session.
  - `PORT` — default 3400; documented so a reverse proxy can be pointed at it.
- One published port; no volumes required (DOT is stateless — every state item lives in the
  runtime or in a browser cookie).
- No dependency on being co-hosted with `swarmkit serve`. `SWARMKIT_URL` can be any reachable
  runtime — same host, another host on the LAN, a tunnelled personal runtime, anything.

**Decision 3.3 — How does the user's browser get to it?**

Same pattern as the fleet UI (`reference_fleet_launch_scripts`): the container binds a port, the
user opens `http://<host>:3400`, DOT's own login flow redirects them to the SwarmKit runtime's
OAuth for authentication (the user signs in to the runtime; DOT gets a session token for that
identity), every API call to the runtime carries the user's identity. No auth reimplemented in
DOT.

A `just dot-up` target at the repo root runs `docker compose up -d` in `reference/apps/dot/`,
matching the ergonomics of `./minder up` and the fleet launch scripts. This is small and worth
having from day one — the operational story is much of what makes a reference app a *reference*.

**Decision 3.4 — What's in the MVP app itself?**

- One page: today's brief. Latest morning-brief run for the signed-in user, rendered as a card
  stack.
- Per-item card with source snippet + buttons for the common per-item actions (Draft reply,
  Retrieve related, Prep response, Summarise thread).
- Right-rail scoped chat surface per item (see the Conversational section below).
- A tiny history strip along the top: last 7 briefs, click to jump back.

That's it for MVP. No settings screen (settings are workspace YAML in the runtime). No
notifications (the run trigger is a schedule in the workspace). No dashboards. The app is small
on purpose — the whole point of the "reference *app*" pattern is that most of the intelligence
lives in the workspace, not the UI.

#### Conversational — the delegation surface

The article's core loop is conversational: *"Draft this email... find that conversation... prep
for the meeting."* The design question is: **what is the conversational surface, and what does it
have access to?**

Three shapes to choose from:

- **Predefined actions per item.** Each item card carries a fixed set of buttons — `Draft reply`,
  `Retrieve related`, `Prep response`, `Summarise thread`. Click → runs a specific per-item
  archetype. No free-form input.
- **Scoped chat per item.** Each item card has an inline chat surface. The user types
  ("draft the reply but push back on the timeline"). That prompt goes to a chat archetype that
  has access to the item's context + the workspace's Gmail/Calendar tools, and runs the
  handle-item topology with the free-form intent as an argument. Not "chat with DOT the AI"; chat
  *about this item*.
- **Both.** Buttons for the 3-5 common cases (fast path, no typing needed), scoped chat for
  everything else. The chat drops the friction floor without making common actions require
  typing.

**Recommendation: both, buttons-first.** The article's user experience is built on speed —
Dominique picks an item and delegates *now*, she doesn't compose a prompt every time. Buttons win
for the common cases; scoped chat covers the long tail.

Mechanism: the per-item chat is not a new primitive. Every SwarmKit archetype that takes a
`prompt` argument can drive this — a `handle-item` invocation with `{item_id, user_intent:
"draft the reply but push back on the timeline"}` compiles to the same topology run a button
would. The chat surface is a small React component in the app; the backend is the runtime we
already have.

**What the conversation is *not*:** it is not a general-purpose assistant. There is no
"Chat with DOT" surface at the top of the app. Every chat is scoped to an item, uses only that
item's context (plus workspace-level Gmail/Calendar access), and produces artefacts (drafts,
prep notes) that the human reviews before anything reaches an outbound channel. The scoping is a
feature — it bounds what a compromised prompt can do to what one specific item is about.

## OAuth path — where the existing seam does the work

The OAuth story is entirely reuse. From [[mcp-oauth]] and the shipped implementation:

1. User opens the portal, navigates to Connections, clicks *Connect Gmail*. The portal (running
   under `serve`) launches Google's OAuth flow, stores the encrypted token in the runtime's
   `oauth_tokens` table keyed by (credential, owner).
2. User does the same for Google Calendar.
3. User runs the `morning-brief` topology (manually, or via a scheduled trigger). The
   `CredentialService` resolves each `source: oauth` reference, refreshes the token on-use if it's
   near expiry, and hands the MCP bundle a live bearer.
4. The MCP bundle calls Google, returns structured results. The runtime records every call in the
   audit log with the resolved credential owner.

Nothing in this path is new work. The design note exists to make the flow visible as a
demonstration, not to design new machinery. The two new pieces are the MCP bundles (curation
YAML pointing at Google's servers) and the workspace YAML wiring them together.

## Self-extending — the recursive authoring loop

The most interesting property of a DOT built on SwarmKit isn't the morning brief. It's that
**the app can extend itself, using SwarmKit's own authoring machinery, at two layers.** This is
SwarmKit's third pillar — "swarms grow through human-approved authoring" — applied recursively.

### Layer 1 — DOT authors new SwarmKit workspace capabilities

The owner, using DOT: *"I want to prep for board meetings differently — pull the deck, the last
three financial reports, the last board pack."*

DOT invokes SwarmKit's `skill-authoring` topology (`reference/topologies/skill-authoring.yaml` —
already shipped). The authoring topology proposes a new archetype + supporting topology, the
owner reviews, publishes to DOT's workspace. Next time an item in the morning brief is a board
meeting, the new archetype runs.

- **Zero framework work to enable this.** `skill-authoring` exists; the runtime knows how to
  execute it; the workspace can be extended live.
- **DOT app's role**: knows how to invoke `skill-authoring` and where in its workspace the
  authored artefacts land. Renders the review step in-app so the owner never leaves the DOT
  container to author.

### Layer 2 — DOT authors its own UI plugins

The owner, using DOT: *"Whenever an item is a customer support ticket, add a button that shows
the customer's account state alongside the ticket."*

**DOT plugins are workspace YAML manifests.** No JavaScript ever ships to extend DOT. A plugin
manifest declares which items it applies to, what button it adds, what topology to invoke on
click, and how to render the result — from a fixed set of `render_as` templates the manifest
chooses from. The DOT app hot-reads its plugin registry from the workspace.

Sketch of the schema (real one is a separate design note — see Future work below):

```yaml
apiVersion: dot/v1
kind: DotPlugin
metadata: { id: board-meeting-prep }
applies_to:
  item_type: meeting                    # 'email' | 'meeting' | 'task' | future kinds
  matches: "board of directors"         # optional regex / prompt-classifier
button_label: "Prep the board pack"
invoke_topology: board-meeting-prep     # a topology in the same workspace
render_as: card_with_attachments        # from a fixed set of built-in templates
```

Adding a Layer-2 capability = author a plugin manifest. **A dedicated `dot-authoring` topology
lives in DOT's workspace** for this purpose. It is authored via `skill-authoring` in the first
place (same primitive; recursion closes cleanly). Once it exists, the owner in DOT can say *"add a
plugin that does X"*, `dot-authoring` produces a manifest + supporting topology, the owner
reviews, DOT hot-reloads.

### Sandbox — authored artefacts are draft until the owner promotes them

**Nothing an authoring topology produces goes live automatically.** This is the load-bearing
safety property for the whole recursive story. An authored archetype, topology, or plugin
manifest lands in a **draft** state; it becomes **active** only after the owner has run it in the
sandbox at least once and explicitly promoted it. Without this, one bad authoring prompt is one
broken morning brief away, and the promise of "extend on demand" turns into "break on demand."

Concrete lifecycle for every authored artefact:

1. **Authored** — the topology (`skill-authoring` or `dot-authoring`) writes the artefact into
   the workspace with a `status: draft` marker. Draft artefacts are visible in the DOT app but
   segregated: a "Drafts" affordance rather than appearing on regular items automatically.
2. **Sandboxed** — the owner triggers the draft explicitly against a chosen item (or a
   past-brief item that has been kept for replay). The runtime executes it in an isolated run —
   same as any other run, but the outputs are labelled "sandbox" and side-effect writes (Gmail
   send, Calendar mutate) are refused by the runtime even in Sprint 6+ where those would normally
   be allowed. Sandboxed runs land in the audit log with an explicit `sandbox: true` flag.
3. **Reviewed** — the owner reads the sandbox output, decides one of:
   - **Promote** — set `status: active`. The DOT app hot-reloads; from now on the plugin fires
     on qualifying items, or the archetype is resolvable by name in the workspace.
   - **Refine** — hand the sandbox output back to the authoring topology as a critique
     (*"the summary missed X, add a step that Y"*), which produces a new draft that supersedes
     the first.
   - **Discard** — delete the draft. No trace beyond the audit log.
4. **Rollback** — an active plugin/artefact can be flipped back to `draft` with a click. Not a
   destructive delete; the definition survives so the owner can refine and re-promote.

This maps onto existing SwarmKit machinery:

- **Canary** — the runtime already has canary topology promotion endpoints (`/canary/*` in the
  HTTP API). The same pattern applies to DOT: an authored topology is a canary until promoted.
- **Approvals queue** — `/api/ops/approvals` already exists; a `promote_to_active` action fits
  naturally as a review item.
- **Audit log** — every draft, every sandbox run, every promote/refine/discard/rollback lands in
  the audit log so nothing about the recursive loop is invisible.

The DOT app's job is to expose all four states as first-class UI: a Drafts drawer, a
one-click sandbox trigger, a promote/refine/discard triple on each sandboxed result, a rollback
affordance on active plugins.

### Why this is architecturally clean, not just clever

- **Both layers use the same primitive.** Workspace YAML + an authoring topology → draft
  artefact → sandbox → owner promotes → active. There is no second mechanism.
- **UI-as-data.** Layer 2 is a direct extension of SwarmKit's topology-as-data pillar into the
  application layer.
- **Governance is uniform.** Every authored artefact — SwarmKit-side or DOT-side — hits the same
  draft/sandbox/promote flow, the same audit log, the same validator. No new governance seam to
  design; the sandbox is a `status` field + a runtime check, not a new subsystem.
- **The sandbox is what makes "self-extending" not "self-breaking."** Without it, recursive
  authoring is a Rube Goldberg machine one prompt away from failure. With it, the failure mode
  of a bad draft is a bad sandbox run — visible, disposable, no impact on the working app.
- **Owner-only removes migration pain.** User-authored plugins in a shared product would need
  schema-migration discipline for every version bump. Owner-only means the owner rewrites their
  own three manifests if the schema changes. Cheap.

## Sprint plan

Because the owner is also the developer and the only user, "phases" collapse into "commits I
merge as they land." What still matters is dependency order:

- **Sprint 1** — MCP bundles (Gmail + Calendar) + read-only workspace (aggregate → rank →
  present) + minimal DOT app (brief page, a fixed handful of built-in per-item buttons).
- **Sprint 2** — **Draft/sandbox/promote lifecycle for workspace artefacts.** The `status: draft`
  marker + the runtime's sandbox-run mode + the app's Drafts drawer + promote/refine/discard/
  rollback affordances. Ships BEFORE any authoring feature lands so the safety property is
  established as invariant, not retrofitted.
- **Sprint 3** — **Layer 1 self-authoring wired into DOT.** DOT can invoke `skill-authoring` to
  produce draft archetypes/topologies. Sandbox lifecycle from Sprint 2 covers them from day one.
- **Sprint 4** — Plugin manifest schema + registry + hot-reload. Fixed `render_as` templates.
  Manifests start as `status: draft` and go through the same sandbox → promote flow. Owner writes
  a plugin manifest by hand first to prove the schema is right against a real case.
- **Sprint 5** — `dot-authoring` topology in the workspace. Recursive loop closes: the owner asks
  DOT to author a new DOT capability, DOT does, it lands as a draft, sandbox runs, owner
  reviews, DOT hot-reloads.
- **Sprint 6+** — send-with-approval and schedule-with-approval, invoking Gmail / Calendar mutate
  scopes with human confirmation before every outbound effect. Sandboxed drafts continue to be
  refused these scopes even in this phase — the sandbox is stricter than production, always.

## Future work — deliberately not this design note

- **The plugin manifest schema** — load-bearing decision; needs its own note before Sprint 4
  code. Draft the schema against three concrete plugin ideas (e.g. board-meeting-prep,
  customer-ticket-context, weekly-writeup) so the schema is shaped by real cases rather than
  imagined ones.
- **The `dot-plugins` repo split** — worth considering when the third or fourth plugin arrives,
  purely for the owner's own tidiness (plugins accumulate; the workspace stays focused on
  topologies and archetypes rather than manifests). Deferred; not driven by any sharing story
  since DOT is owner-only.
- **The `render_as` template catalogue** — starts as ~three built-ins (`card_with_attachments`,
  `card_with_metrics`, `card_with_chat`); grows as plugin cases push for new shapes. Every new
  template is a small first-party addition; plugins never define their own rendering code.
- **Additional per-item archetypes** — the workspace layout in section 2 is a starting set;
  richer item types (support tickets, PRs, standups) get their own archetypes as the owner
  authors them via Layer 1.

## Eight decisions the workspace owner needs to make before code

Every question has a proposed default. Q5 is committed rather than a proposal — the direction is
decided.

- **Q1 — Where does the workspace live?**
  Proposed: `reference/workspaces/dot/` in this repo (canonical, discovered alongside the other
  reference topologies). Alternative: a separate `dot-workspace` sibling repo (freer to iterate,
  harder to find). *Default: in-repo.*

- **Q2 — MVP scope: read-only or read-write?**
  Proposed: **read-only for Sprint 1** as described in the Sprint plan. *Default: read-only.*

- **Q3 — Providers for the first pass.**
  Proposed: **Gmail + Google Calendar, nothing else.** Slack, Notion, GitHub, Linear are natural
  next additions in the same shape but expanding the initial surface muddies the OAuth
  demonstration. *Default: Gmail + Calendar only.*

- **Q4 — LLM provider.**
  DOT context is private. Options: (a) local Ollama for full privacy, (b) cloud Claude/GPT with
  the acknowledgement that inbox contents leave the box, (c) both configurable via the workspace.
  Proposed: **(c) — pin a default in the reference YAML but leave the provider swappable**, per
  the existing `ModelProvider` seam. Default in the reference YAML: **local Ollama**, so the
  demonstration works without a cloud key and the privacy story leads. *Default: local Ollama with
  a documented cloud override.*
  Note: the shipped authoring topology (`authoring-supervisor`) already defaults to
  `openrouter/moonshotai/kimi-k2.5` — LLM defaults are per-topology, not global. DOT's own
  topologies pick their own defaults independently.

- **Q5 — Who is the user for MVP?**
  **Committed: owner-only** for the foreseeable future. Not planning around a public user base;
  self-authoring is the point of the experiment, and the sandbox mechanism (above) is what makes
  it safe for one user rather than what it would need to be safe for many.

- **Q6 — Where does the app's source live?**
  Frontend is definitely a separate app, not a portal panel — that decision was made after review
  (DOT is a product, not a SwarmKit feature; the portal is for platform observability). What's
  open: (A) `reference/apps/dot/` in this repo, establishing a new "reference app" pattern; (B)
  `apps/dot/` in swarmkit-skills, wrong-shape fit for a catalogue; (C) its own `dot-app` repo,
  full independence. *Default: A — peer package in this repo, deploys separately.*

- **Q7 — Conversational surface.**
  Options: (a) buttons-only per item, (b) scoped chat only per item, (c) both, buttons-first.
  Neither is a general-purpose "Chat with DOT" surface — the scoping is a feature. *Default: c —
  buttons for common actions, chat for the long tail.*

- **Q8 — Sandbox mechanism for authored artefacts.**
  Options: (a) reuse SwarmKit's existing canary mechanism (`/canary/*` endpoints) — draft
  artefacts are canary-promoted; free but constrained to canary's shape; (b) add a `status`
  field to each authored artefact + a runtime sandbox-run mode + a promote/refine/discard flow —
  more work now, purpose-built for the DOT case; (c) both — canary for topologies, `status` for
  DOT plugin manifests. Proposed: **(c)** — canary is the right shape for topology-level
  artefacts and already ships; DOT plugin manifests are simpler and don't need canary's full
  weight. *Default: c — canary for SwarmKit-side artefacts, status field for DOT-side.*

## Split for reviewability

Per the umbrella issue #990, ten PRs mapping onto the Sprint plan above (Sprint column shown):

| PR | What ships | Sprint |
|---:|---|:---:|
| 1 | This design note (design-only PR, reviewed before implementation) | — |
| 2 | `swarmkit-skills/gmail` bundle — pack.yaml pointing at Google's remote server (with community fallback), OAuth wiring against SwarmKit's existing `source: oauth`, nightly probe | 1 |
| 3 | `swarmkit-skills/google-calendar` bundle — same shape, Calendar surface | 1 |
| 4 | `reference/workspaces/dot/` scaffold: aggregator + ranker + morning-brief topology + handle-item sub-topology + per-item archetypes + HITL gate scaffold (no-op in read-only) | 1 |
| 5 | `reference/apps/dot/` — standalone Next.js app (peer to `packages/ui/`), hosted Docker image `ghcr.io/delivstat/dot-app`, Dockerfile in tree, `docker-compose.yml` (default + `all-in-one` profiles), `just dot-up` target. Establishes the "reference app" pattern; introduce it with a paragraph in `README.md` | 1 |
| 6 | **Sandbox lifecycle for authored artefacts** — `status: draft` marker + runtime's sandbox-run mode (side-effect writes refused) + audit log `sandbox: true` flag. Ships BEFORE any authoring feature so the safety property is invariant. Canary for topologies (already shipped) + `status` field for DOT plugin manifests (per Q8) | 2 |
| 7 | **Layer 1 authoring wired into DOT** — DOT app invokes `skill-authoring`; drafts land in the Drafts drawer; sandbox / promote / refine / discard / rollback affordances | 3 |
| 8 | **DOT plugin manifest schema + registry + hot-reload** — schema drafted against three concrete plugin ideas (board-meeting-prep, customer-ticket-context, weekly-writeup); `render_as` template catalogue starts at ~3. **Own design note precedes this PR** | 4 |
| 9 | **`dot-authoring` topology + Layer 2 recursive loop closed** — owner asks DOT to author a new DOT capability, `dot-authoring` produces a draft plugin manifest, sandbox → promote → hot-reload | 5 |
| 10 | `docs/dot-quickstart.md` + `just demo-dot` fixture-based demo | 1 (finalised across sprints) |

The app (PR 5) is the piece most likely to iterate after first use — deliberately scoped small
so we can rewrite it as the recursive loop teaches us what an "extend-me" DOT app actually needs.

## Test plan

Baseline coverage — Sprint 1:

- Contract tests for the two MCP bundles against Google's own API sandbox / recorded responses.
- End-to-end test for the `morning-brief` topology against a fixture workspace with fake
  credentials and pre-canned MCP responses; asserts the ranked list has expected shape and cites
  the correct source items.
- Approval-gate test scaffolding (asserts that no outbound effect fires in read-only Sprint 1
  even when the topology tries; there is nothing to try yet, so this test starts as a placeholder
  that becomes load-bearing at Sprint 6+).

Sandbox + authoring coverage — Sprints 2–5:

- **Draft isolation.** An artefact with `status: draft` does NOT fire on qualifying items;
  proven by seeding a draft plugin and asserting the morning brief renders without its button.
- **Sandbox refuses side-effects.** A sandbox run of a topology that tries to send an email or
  mutate a calendar entry is refused by the runtime, even in Sprint 6+ where those scopes would
  normally be allowed for a non-sandbox run.
- **Promote flips status.** After promote, the same artefact fires on qualifying items on the
  next reload.
- **Rollback works.** Flipping back to draft removes the artefact from active behaviour but
  keeps the definition on disk.
- **Authoring end-to-end.** `skill-authoring` produces a draft; the DOT app surfaces it; sandbox
  run produces an output; promote makes it live. Same test structure for `dot-authoring` when
  Sprint 5 lands.
- **Audit trail completeness.** Every state transition (authored / sandboxed / promoted /
  refined / discarded / rolled-back) writes a distinguishable audit entry.

## What this earns SwarmKit

Beyond the immediate utility of a personal DOT:

- **Live proof of per-user OAuth end-to-end.** Portal → provider consent → token store →
  refresh-on-use → per-owner audit. Every one of those pieces is shipped; none has had a flagship
  reference workspace exercising them until now.
- **A pattern for personal-workflow workspaces.** The `aggregate → rank → present → delegate on
  approval` shape is domain-agnostic. Once the shape is a reference workspace, replacing "Gmail +
  Calendar" with "Linear + GitHub" is a workspace edit, not a framework change.
- **The "reference app" pattern established.** Reference workspaces have wanted their own UIs
  for a while but nothing has landed one; DOT is the first, and `reference/apps/` becomes a
  discoverable place other reference workspaces can drop UIs when they want them.
- **Evidence for the platform story.** The claim that SwarmKit is a runtime for governed AI
  systems needs a reference that is neither a code-review tool (`code-review.yaml`) nor a
  memory-curator (`knowledge-curator.yaml`) — something in the personal-productivity domain where
  the OAuth surface is the load-bearing feature. DOT is that reference.
