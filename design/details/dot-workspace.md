# DOT — the personal-workflow reference workspace

**Status:** design (issue #990).
**Design references:** [[mcp-oauth]] for the per-user OAuth binding; §8.5 (`GovernanceProvider`),
§8.7 (structural approval scopes) for the HITL gates. The DOT concept originates in
[*How Dominique Rebuilt Her Morning Around AI*](https://www.linkedin.com/pulse/how-dominique-rebuilt-her-morning-around-ai-chaithra-madan-qpmbc/)
by Chaithra Madan — the mechanism there is generic; what this note designs is a SwarmKit
implementation of it as a shipped reference.

## Goal

A reference workspace, shipped as YAML, that pulls a user's morning context (inbox, calendar,
recent conversations), surfaces a ranked list of actionable items, and — with human approval —
delegates each item to a small archetype (draft, retrieve, summarise, propose). Two things at
once:

1. A genuinely useful personal-productivity tool for someone who copies the workspace and connects
   their accounts.
2. The flagship demonstration of SwarmKit's per-user OAuth surface (#976, #982) — currently
   shipped, currently unshowcased.

The mechanism is the point. If everything a DOT does can be expressed as topology + archetypes +
credentials + HITL gates, then SwarmKit is the right platform for a whole class of personal
workflow tools, and DOT is the first.

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
- **Not send-first.** The MVP is deliberately read-only. Read-write staging is called out below
  and is a separate design conversation.

## Shape

Three artifacts, in dependency order:

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
    matches Phase 1's read-only stance exactly; when Phase 2 lands and we need sending, we either
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
    actually consume from the workspace (\`list_recent\`, \`get_message\`, \`search\`), and a nightly
    liveness probe (per \`feedback_skills_catalogue_curation\` — probe don't transcribe).
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
  workspace.yaml            # credentials, mcp_servers, provider settings
  archetypes/
    context-aggregator.yaml # pulls last-24h context from Gmail + Calendar
    item-ranker.yaml        # decision skill: rank + structured output
    email-drafter.yaml      # draft in the user's tone (delegated per item)
    meeting-prepper.yaml    # prep notes for an upcoming meeting (delegated)
  topologies/
    morning-brief.yaml      # the top-level swarm: aggregate -> rank -> present
    handle-item.yaml        # invoked per item after human picks it
```

The morning-brief topology has a `supervisor-leader` root, a `context-aggregator` worker (calls
Gmail + Calendar MCPs), and an `item-ranker` worker whose output is a structured list of ≤10
items. The output is committed to the audit trail and surfaced to the human (where — see the
frontend section below); no autonomous action is taken.

The handle-item topology is invoked when the human picks an item. It takes the item id + the
aggregator's context and dispatches to the right per-item archetype (draft an email, prep a
meeting, retrieve a past conversation). Each per-item action goes through a HITL gate before any
outbound effect.

### 3. Frontend — what the user actually sees

**In read-only Phase 1, without a UI, the artifacts already exist.** A morning-brief run produces:

- A ranked list of ≤10 items with source references (message ids, event ids, thread ids).
- A structured JSON output committed to the audit log.
- Per-item context bundles the handle-item topology can pick up.

That is enough to be inspectable in the portal's existing runs view. It is not enough to be
*useful* — nobody starts their morning by reading a JSON blob in a run tree. So the frontend
decision is between three real options:

- **Option A — portal-mounted panel.** Add a first-class `Brief` tab to `swarmkit-webui` that
  renders the latest morning-brief output for the signed-in user, with per-item click-through to
  invoke the handle-item topology. Cheapest to build; lives with the runtime; benefits every user
  who runs DOT. Bounds what the panel can do to what the portal already knows how to render.
- **Option B — separate first-party app.** A dedicated SPA (call it `dot-app`) sitting alongside
  the portal, using SwarmKit's HTTP API as its backend the same way a product built on the
  runtime would. Freer to iterate on the UX (morning-ritual affordances, notifications, per-item
  chat), harder to keep in step with runtime API changes, adds a repo to maintain. This is the
  shape products like Minder use — swarmkit-runtime as a library + a domain-specific webapp on
  top.
- **Option C — start with A, plan for B.** Ship the portal panel first (unblocks personal use,
  validates the shape), extract to a standalone app when the UX diverges enough from the portal
  that the panel starts fighting its container.

**Recommendation: C.** The frontend is the fastest to iterate part of the whole project, and the
worst possible mistake is to build a full app around a workspace before the workspace shape is
right. A portal panel forces us to defend "would we really want a separate app for this?" every
iteration — a question worth asking many times before answering yes. The panel is cheap; extract
it only when it earns extraction.

Concretely for MVP:

- One Next.js page in `packages/ui/` (the existing portal) at route `/dot`, feature-flagged
  behind the presence of the DOT workspace in the runtime.
- Renders the latest morning-brief run for the signed-in user (uses the existing `/api/runs`
  endpoint filtered by `topology_id=morning-brief`).
- One card per item, each with a `[Handle]` button that POSTs to the runtime's run-start endpoint
  with the handle-item topology + the picked item id.
- A right-rail per-item chat surface (see next section).

The app-shaped extraction (Option B) becomes its own design note if and when the panel starts
feeling like a container fight.

### 4. Conversational — the delegation surface

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
would. The chat surface is a small React component in the panel; the backend is the runtime we
already have.

**What the conversation is *not*:** it is not a general-purpose assistant. There is no
"Chat with DOT" surface at the top of the app. Every chat is scoped to an item, uses only that
item's context (plus workspace-level Gmail/Calendar access), and produces artifacts (drafts,
prep notes) that the human reviews before anything reaches an outbound channel. The scoping is a
feature — it bounds what a compromised prompt can do to what one specific item is about.

### 5. Documentation + demo

- `docs/dot-quickstart.md` — how to connect Gmail + Calendar through the portal, run the morning
  brief, use the panel, inspect the run tree.
- `just demo-dot` — end-to-end target that runs against a demo account fixture (a small pre-canned
  set of inbox + calendar JSON responses); no real accounts needed for CI.

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
demonstration, not to design new machinery. The two new pieces are the MCP bundles' code (call
Google's APIs) and the workspace YAML wiring them together.

## Read-only first — the staging that matters

The MVP is deliberately read-only. That is the honest scope for a first ship, and it lets the DOT
prove out the pattern before we design the approval gates for outbound action.

Progressive path:

- **Phase 1 (this design note):** read-only. Aggregate, rank, present. No sending, no scheduling,
  no calendar mutation. HITL gate is a no-op because there is no side-effect to approve.
- **Phase 2 (a follow-up design note):** send-with-approval. `email-drafter` produces a draft; the
  HITL gate requires an approval scope reserved for a real human before the draft is sent through
  a Gmail `send` skill. This is where the `GovernanceProvider` earns its keep in a personal
  workflow context.
- **Phase 3:** schedule-with-approval. Same shape for calendar mutations.

The reason for staging: sending an email in a user's name is a large-blast-radius action, and the
design of the approval flow deserves its own note. Shipping read-only first tests every part of
the plumbing without carrying that risk.

## Five decisions the workspace owner needs to make before code

Every question has a proposed default so the code path is unambiguous if the owner just says "go."

- **Q1 — Where does the workspace live?**
  Proposed: `reference/workspaces/dot/` in this repo (canonical, discovered alongside the other
  reference topologies). Alternative: a separate `dot-workspace` sibling repo (freer to iterate,
  harder to find). *Default: in-repo.*

- **Q2 — MVP scope: read-only or read-write?**
  Proposed: **read-only for Phase 1** as described above. *Default: read-only.*

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

- **Q5 — Who is the user for MVP?**
  Options: (a) the workspace owner personally (real run against real accounts, evidence the
  platform works), (b) a public reference (people copy and run). Proposed: **(a) first, then (b)**
  — the reference topology gets published from the first working run rather than being written
  speculatively. *Default: owner-first.*

- **Q6 — Frontend shape.**
  Options as described in the Frontend section: (A) portal-mounted panel now, (B) separate first-
  party app now, (C) portal panel now → extract to standalone app when the UX diverges enough to
  justify the container fight. *Default: C — portal panel now, plan for extraction.*

- **Q7 — Conversational surface.**
  Options: (a) buttons-only per item, (b) scoped chat only per item, (c) both, buttons-first.
  Neither is a general-purpose "Chat with DOT" surface — the scoping is a feature. *Default: c —
  buttons for common actions, chat for the long tail.*

## Split for reviewability

Per the umbrella issue #990, updated after MCP-ecosystem discovery and the frontend decision:

1. This design note (design-only PR, reviewed before implementation).
2. `swarmkit-skills/gmail` bundle — pack.yaml pointing at Google's remote server (with community
   fallback), OAuth wiring against SwarmKit's existing `source: oauth`, nightly probe.
3. `swarmkit-skills/google-calendar` bundle — same shape, Calendar surface.
4. `reference/workspaces/dot/` scaffold: aggregator + ranker + morning-brief topology + handle-item
   sub-topology + per-item archetypes + HITL gate scaffold (no-op in read-only).
5. `packages/ui/` — the `/dot` panel: latest morning-brief render, per-item buttons for the common
   actions, scoped chat surface for freeform intent. Feature-flagged on workspace presence.
6. `docs/dot-quickstart.md` + `just demo-dot` fixture-based demo.

Each PR references this design note. The panel (PR 5) is the piece most likely to iterate after
first use — deliberately scoped small so we can rewrite it once we have real morning-run
experience.

## Test plan

- Contract tests for the two MCP bundles against Google's own API sandbox / recorded responses.
- End-to-end test for the `morning-brief` topology against a fixture workspace with fake
  credentials and pre-canned MCP responses; asserts the ranked list has expected shape and cites
  the correct source items.
- Approval-gate test scaffolding (asserts that no outbound effect fires in Phase 1 even when the
  topology tries; there is nothing to try yet, so this test starts as a placeholder that becomes
  load-bearing when Phase 2 lands).

## What this earns SwarmKit

Beyond the immediate utility of a personal DOT:

- **Live proof of per-user OAuth end-to-end.** Portal → provider consent → token store →
  refresh-on-use → per-owner audit. Every one of those pieces is shipped; none has had a flagship
  reference topology exercising them until now.
- **A pattern for personal-workflow topologies.** The `aggregate → rank → present → delegate on
  approval` shape is domain-agnostic. Once the shape is a reference topology, replacing "Gmail +
  Calendar" with "Linear + GitHub" is a workspace edit, not a framework change.
- **Evidence for the platform story.** The claim that SwarmKit is a runtime for governed AI
  systems needs a reference that is neither a code-review tool (`code-review.yaml`) nor a
  memory-curator (`knowledge-curator.yaml`) — something in the personal-productivity domain where
  the OAuth surface is the load-bearing feature. DOT is that reference.
