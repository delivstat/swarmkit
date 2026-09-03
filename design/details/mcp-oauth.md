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
- **Storing a user's password, ever.** Authorization-code with PKCE only. No device-code flow in v1
  either — it exists for input-constrained devices, and a portal is a browser.
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
