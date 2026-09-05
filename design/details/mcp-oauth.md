---
title: OAuth for remote MCP servers — logging in from the portal, and staying logged in
description: A browser login flow that produces a usable token, refresh that happens before a run rather than during it, and expiry that is announced rather than discovered.
tags: [mcp, auth, oauth, ui]
status: draft
---

# OAuth for remote MCP servers

**Scope:** `swarmkit serve` (routes + portal), the credential store, `mcp_servers`
**Depends on:** 1.204.0, where `credentials_ref` began reaching the server at all
**Status:** draft

## Goal

Let someone add a remote MCP server from the portal, log in to it in their browser, and have runs
keep working afterwards without anyone thinking about tokens again — until the day thinking about
them is genuinely required, at which point say so clearly and early.

## Why this is not just "add a login button"

1.204.0 made a declared credential reach the server. It did not answer where a browser-obtained
token *comes from*, and three questions turn out to be decisions rather than details:

- **Whose token is it?** The portal is one origin several people can open.
- **Where does it live?** `credentials` entries are `env:` or `file:` refs. A token fetched in a
  browser has neither.
- **What happens when it expires?** Which is the question this note spends most of its length on,
  because the obvious answer is the wrong one.

## Whose token: the viewer's, and the workspace never sees it

A token obtained in a browser belongs to **the person who logged in**. Storing it as a workspace
credential silently converts one person's GitHub access into everyone's — and the audit log would
then record the swarm doing things "as the workspace" when it was really acting as a named human
who may not know a scheduled run is using their account at 3am.

So: **credentials are per-owner from the first version**, keyed by the authenticated identity that
`serve` already resolves (`/whoami`). A run started by a schedule rather than a person uses a
credential explicitly designated for unattended use, and designating one is a deliberate act with
its own audit entry.

This costs a field now and a migration across every stored token later, which is the same argument
that put an owner on scripts before multi-tenancy existed.

## Where it lives: a new source, not a new store

`credentials` already dispatches on `source` — `env`, `file`, and cloud providers behind a
`SecretsProvider` seam. A browser-obtained token is a fourth source:

```yaml
credentials:
  linear:
    source: oauth
    config:
      provider: linear                  # an entry in the OAuth provider registry
      owner: srijith@delivstat.com      # whose token; required
```

The token itself is never in `workspace.yaml` — the entry is a reference, exactly like the others.
The bytes live in the runtime's own encrypted store beside the audit and run state.

## What the connection binds to: the server, not the skill, archetype or topology

The question this note originally skipped, and the first one anybody asks: if a skill is used by
three archetypes across five topologies, whose login does it use? Is the token per-skill,
per-archetype, or per-topology?

**None of them. It binds to the `mcp_servers` entry.** The isolation people are reaching for comes
from having more than one named connection, not from a new binding level.

### Why not on the artifacts

**Artifacts stay portable — that is invariant #1 and #7, not a preference.** Skills, archetypes and
topologies are portable open data any conformant runtime can run. Put a token, or even a token
*reference*, on a skill and that artifact stops being portable: exporting it either carries a
credential or produces something that will not run. This is already the design rather than an
oversight — `skill.schema.json` has no credential field at all, and neither does archetype or
topology. Credentials live in the workspace precisely so everything above them travels.

**A token is not an authorization boundary; scopes are.** Binding a token to an archetype in order
to stop a different archetype reaching Linear builds a second access-control system the policy
engine cannot see. *May this agent write to Linear?* would then have two answers in two places, and
the invisible one would win. `iam.required_scopes` authorizes and the action string is only a label
(§8.5); a per-archetype token would quietly become the real gate, outside governance and outside
the audit log.

**Refresh multiplies.** Refresh happens before a run (below), so one connection is one refresh loop.
Per-topology binding gives N tokens to keep alive, N ways to be expired when a schedule fires at
3am, and N consent prompts with nobody awake to answer them.

**The provider already scoped the grant.** Scopes are approved at the remote service. A per-topology
token is no finer-grained than that grant unless it comes from a separate login with different
scopes — which is a *connection* distinction, not a topology one.

### Where the isolation actually comes from

```yaml
mcp_servers:
  - id: linear-prod
    credentials_ref: linear-prod-oauth
  - id: linear-sandbox
    credentials_ref: linear-sandbox-oauth
```

| the isolation wanted | how it is expressed |
| --- | --- |
| per-skill | the skill's `implementation.server` names its own connection |
| per-archetype | grant that archetype only skills bound to that connection |
| per-topology | the same, through which agents the topology instantiates |

All three, in the mechanism that already exists, with governance still able to see the whole
picture. It is the same move as `channels:` being named rather than one global destination
(`channel-skills.md`): the distinction is real, and it belongs on the connection.

**If it must be enforced rather than conventional** — *agents in this topology may only use the
sandbox connection* — that is a contract or a policy rule over server ids, not a token binding.
Authorization stays in one place.

### What the UI does with this

The OAuth surface is a **Connections** page: one row per `mcp_servers` entry that needs auth, with
Connect, the owner, the granted scopes, the expiry and Reconnect. Skill, archetype and topology
pages show *which connection a skill resolves to*, read-only, so the blast radius of granting it is
visible before it is granted. There is no token control on those three pages: someone who wants a
different token adds a connection and points a skill at it.

## Refresh: before a run, not during one

Here is the part worth arguing about.

The obvious design is to refresh reactively: call the server, get a 401, refresh, retry. It works,
and it is the wrong shape for this system, because **a run that will fail at minute eight because a
token expired at minute three should have been dealt with at minute zero.** That is the same
principle as `requires_runtime` checked at workspace load and a command pack's `requires` checked
before anything runs — a class of bug this repo keeps rediscovering and keeps fixing the same way.

Three layers, in the order they should fire:

**1. Ahead of time — announce, do not discover.** Expiry is knowable in advance. A scheduled check
warns while there is still time to act, rather than at 3am when a nightly run fails. A refresh token
with a week left is a notification; one with a day left is a review-queue item.

**2. Before a run — refresh if it will be needed.** At run start, any credential the topology may use
whose access token expires inside the run's plausible window is refreshed *then*, silently, using the
refresh token. This is where almost all refreshing should happen, and it costs one round trip against
a run that is about to make many.

**3. During a run — only if it can be silent.** A refresh that needs no human interaction proceeds
and is audited. A refresh that needs consent — the refresh token is revoked, expired, or the scope
changed — **must not block the run holding a session open.** It parks it.

## A token needing consent is a human gate, and we already have one

This is the piece that makes the design small rather than large.

SwarmKit already parks a run on a human: a funnel's `approve` layer checkpoints, the job goes
`deferred`, nothing stays resident, and `POST /jobs/{id}/resume` continues it once the decision
arrives. A credential needing re-consent is *the same shape* — a run that cannot proceed until a
named person does something in a browser.

So it becomes a **review-queue item**, resolved through the surface the CLI, the portal and the fleet
UI already share, rather than a new kind of blocked state that each front-end has to learn:

```
run → tool call → credential 'linear' needs consent
    → checkpoint, job deferred
    → review item: "log in to Linear to continue run 8f2a"  → notification
    → person clicks through the OAuth flow
    → POST /jobs/8f2a/resume
```

Nothing new is invented. The alternative — a bespoke "credentials expired" state — would need its own
UI, its own notification path, and its own resume semantics, all of which exist already.

## Every refresh is in the audit log

A refresh is an action taken **on a person's behalf, with their identity**, and the audit log is the
only place that can later answer *"why did the swarm act as me at 3am?"* So each one records the
credential, the owner, the trigger (`scheduled` / `pre-run` / `mid-run`), and the outcome — never
any part of either token.

## The refresh token is the dangerous one

An access token expires; a refresh token is a standing grant. Three rules:

- **Refresh tokens are encrypted at rest and never leave the runtime.** They are not readable
  through any HTTP route, including to their own owner — a token you can read is a token that can be
  exfiltrated by anything that can read as you.
- **`/credentials` returns metadata only** — provider, owner, expiry, last refresh. Never bytes.
- **Revocation is local and remote.** Deleting a credential revokes upstream where the provider
  supports it. A deletion that leaves a live grant on someone else's server is not a deletion, which
  is the same standard `face_store.delete_person` is held to.

## The CLI is not the fallback — it is the common case

An earlier draft of this note was portal-first and listed the device authorization grant as a
non-goal, on the reasoning that *"it exists for input-constrained devices, and a portal is a
browser."* **That was wrong**, and it contradicts SwarmKit's own position that the CLI is the
on-ramp and the portal extends it.

The constrained case is not an input-constrained *device*. It is a **browser-less host**, which is
the normal place to run a server-side runtime: a VPS, an appliance, a CI job, a container. `gh auth
login` and `aws sso login` both solve exactly this, and they solve it with a device grant.

Three CLI paths, in descending order of how often they are what someone wants:

**1. A token you already have — works today, no new feature.** Most "OAuth" servers accept a
long-lived personal token, and `credentials` has always had `env:` and `file:` sources. Since
1.204.0 they reach the server:

```yaml
credentials:
  linear: { source: env, config: { env: LINEAR_TOKEN } }
mcp_servers:
  - id: linear
    transport: http
    endpoint: https://mcp.linear.app/mcp
    credentials_ref: credentials:linear
```

```bash
export LINEAR_TOKEN=lin_api_…
swarmkit run ./workspace my-topology
```

No portal, no flow, no browser. This should be the documented default, and the note previously did
not mention it at all.

**2. A device grant, for a real OAuth login with no local browser.**

```
$ swarmkit auth login linear
  open https://linear.app/device and enter  WDJB-MJHT
  waiting…                                  ✓ authorised as srijith@delivstat.com
  stored as credentials:linear (expires in 8h, refresh token stored)
```

The browser can be on a phone, or a laptop, while the runtime is on a box in a rack. That is the
whole point of the grant, and it is why it belongs here rather than being excluded.

**3. Paste a token obtained elsewhere.** `swarmkit auth set linear --stdin`, for a provider with no
device grant. Deliberately last: it works, and it encourages pasting secrets into shell history if
offered first.

Everything after this section — refresh, parking, per-owner storage, audit — is **identical for all
four paths**, portal included. The flow that obtained a token is not a property of the token.

## The portal flow

```
Settings → MCP servers → Add remote server
  1. paste the endpoint            https://mcp.linear.app/mcp
  2. probe                          unauthenticated GET → the provider's OAuth metadata
  3. "Log in to Linear"             authorization-code + PKCE, in a popup
  4. callback                       GET /auth/mcp/callback → exchange → store, per owner
  5. discovered tools               list_tools with the new token, shown for confirmation
  6. write                          diff of the mcp_servers + credentials entries, applied on confirm
```

Step 5 is the one that earns its place. The point of a login flow is not that a token was obtained —
it is that **the server answers**. Showing the tool list is proof, and it is also the moment to
choose which tools become skills, which is the curation judgement `skill-catalogue-seed.md` leaves
open for servers like GitHub that expose fifty-one.

Step 6 shows a diff and asks, because `workspace.yaml` is hand-authored. Same rule as
`swarmkit skill add`.

## Non-goals

- **Being an identity provider.** SwarmKit is an OAuth *client*. Its own inbound auth (JWT/JWKS,
  API keys) is a separate concern and stays separate.
- **Storing a user's password, ever.** Authorization-code with PKCE in the browser; device
  authorization grant on the CLI. Never a password.
- **Sharing one token across owners.** An unattended credential is designated explicitly; it is
  never the accidental result of one person logging in.
- **Silent re-consent.** If a provider needs a human, a human is asked. The run parks.

## Test plan

- **Unit** — PKCE challenge/verifier; state parameter rejected on mismatch (the CSRF case);
  expiry-window arithmetic, including a token that expires *during* the plausible run window.
- **Unit** — refresh classification: silent-possible vs consent-required, from the provider's error.
- **Integration** — a stub OAuth provider: full authorization-code exchange, a refresh, a revoked
  refresh token producing a review item rather than a failed run.
- **Integration** — a run parked on consent resumes after the item is resolved, and does not re-run
  the work it already did (the same property `approve`-layer defer already has).
- **Security** — no route returns a refresh token; the audit records a refresh and contains neither
  token; deleting a credential attempts upstream revocation.

## Demo plan

`just demo-mcp-oauth` against a stub provider: add a server, log in, see the discovered tools, run a
topology that uses it, expire the access token, watch the pre-run refresh happen silently — then
revoke the refresh token and watch the run park with a review item instead of failing.

The revocation is the demo. Anyone can show a login working; the claim worth demonstrating is what
happens on the day it stops.

## Open questions

1. **How long is "the run's plausible window"?** Pre-run refresh needs an estimate of how long a run
   will take, and topologies vary from seconds to hours. A conservative constant is the obvious
   start; a per-topology p95 from the run history is the better answer once there is history.
2. **Provider registry shape.** Endpoints and scopes per provider have to live somewhere. The MCP
   spec's authorization discovery may make this unnecessary for compliant servers, and hand-written
   for the rest.
3. **What happens to a scheduled run whose designated credential needs consent?** There is no human
   attached to a trigger. Probably: park, notify the designated owner, and let the schedule skip
   rather than queue — a queue of parked nightly runs is a pile nobody drains.
4. **Fleet.** A token obtained on one instance is not available on another. Out of scope here, and
   the control plane is where it would belong.
5. **Which owner does a given run use?** Credentials are per-owner (above) and the connection is
   per-server, so the remaining variable is neither: it is *whose* token a particular run presents.
   That is a property of the run, not of any artifact — an interactive run can use the identity of
   whoever started it, while a scheduled one has no human and must use a credential explicitly
   designated for unattended use. Likely home: the trigger for scheduled runs, and the resolved
   caller identity for interactive ones. This is usually what people mean when they ask whether
   OAuth is per-topology, and it is worth answering before the Connections page ships, because the
   page has to show which owner a connection belongs to.
