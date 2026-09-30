# DOT — the personal-workflow reference workspace

**Status:** design (issue #990).
**Design references:** [[mcp-oauth]] for the per-user OAuth binding; §8.5 (`GovernanceProvider`),
§8.7 (structural approval scopes) for the HITL gates. The DOT concept originates in the "Pro User
Files" article on the Daily Optimization Tracker — the mechanism is generic; the topology is the
point.

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
- **Not Minder-shaped.** Minder is a product built with SwarmKit and lives in its own repo. DOT is
  a reference workspace + supporting MCP bundles, distributed like `code-review.yaml` and
  `knowledge-curator.yaml` — in-tree.
- **Not a Gmail / Google Calendar reimplementation.** The MCP bundles wrap Google's own APIs
  through the OAuth flow SwarmKit already supports; they add no business logic.
- **Not send-first.** The MVP is deliberately read-only. Read-write staging is called out below
  and is a separate design conversation.

## Shape

Three artifacts, in dependency order:

### 1. Two OAuth-authenticated MCP bundles (in `swarmkit-skills`)

Neither exists today. Currently the catalogue has 13 bundles / 40 skills and no Google surface.

- **`gmail`** — read-only MVP:
  - `list_recent(hours=24, limit=50)` → summary list of recent messages
  - `get_message(id)` → full body, headers, attachments-list
  - `search(query, limit=20)` → Gmail search syntax passthrough
  - OAuth scope: `gmail.readonly`
- **`google-calendar`** — read-only MVP:
  - `list_events(window="today+tomorrow", calendar="primary")` → events in a window
  - `get_event(id)` → full event with attendees, description, conferencing
  - `find_free(participants[], duration_minutes, window)` → free-slot search (read-only, does not
    schedule)
  - OAuth scope: `calendar.readonly`

Both bundles use `source: oauth` with `identity: per-user` (from [[mcp-oauth]]); a workspace using
them requires a per-user connection through the portal before any run.

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
items. The output is committed to the audit trail and surfaced to the human via the portal — no
autonomous action taken yet.

The handle-item topology is invoked when the human picks an item. It takes the item id + the
aggregator's context and dispatches to the right per-item archetype (draft an email, prep a
meeting, retrieve a past conversation). Each per-item action goes through a HITL gate before any
outbound effect.

### 3. Documentation + demo

- `docs/dot-quickstart.md` — how to connect Gmail + Calendar through the portal, run the morning
  brief, inspect the run tree.
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

## Split for reviewability

Per the umbrella issue #990, six PRs:

1. This design note (design-only PR, reviewed before implementation).
2. `swarmkit-skills/gmail` bundle (OAuth + `list_recent` + `get_message` + `search`).
3. `swarmkit-skills/google-calendar` bundle (OAuth + `list_events` + `get_event` + `find_free`).
4. `reference/workspaces/dot/` scaffold: aggregator + ranker + morning-brief topology.
5. `handle-item` topology + per-item archetypes + HITL gate scaffold (still no-op in read-only).
6. `docs/dot-quickstart.md` + `just demo-dot` fixture-based demo.

Each is small enough to review; each PR references this design note.

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
