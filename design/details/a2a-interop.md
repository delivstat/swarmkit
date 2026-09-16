---
title: A2A interop — every topology is an A2A agent, and a remote A2A agent is a skill
description: Serve publishes an Agent Card and the A2A task API for each topology, mapped onto the existing job model (submitted→working→input-required→completed) so governance is untouched; a remote A2A agent is callable as a skill implementation type. The first real A2A work — earlier notes named it but nothing shipped.
tags: [runtime, serve, interop, a2a, skills, standards]
status: proposed
---

# A2A interop — every topology is an A2A agent, and a remote A2A agent is a skill

**Scope:** `packages/runtime` (serve routes, skills), `packages/schema` (skill implementation type)
**Design reference:** §18 (MCP integration — A2A is its agent-level peer), §12 (skills as the only extension primitive), §8.7 (gates are structural)
**Status:** proposed

## Where this actually stands (read first)

A2A has been *named* in this repo since v0.2 and implemented nowhere. What exists today:

- The v0.6 doc says leaders "communicate laterally via A2A" and folds A2A handoffs into
  coordination skills.
- `reference/skills/peer-handoff.yaml` — an `llm_prompt` skill described as "the canonical
  coordination skill for A2A communication". It packages context as text. No protocol.
- `adk-lessons.md` and `fleet-control-plane.md` say M18 "builds on the existing A2A adapter
  (`_delegation.py`)". **There is no A2A code in `_delegation.py`** — it is the compiler's internal
  child-delegation. That sentence was aspiration written as fact; both notes are corrected in
  this PR.
- `IMPLEMENTATION-PLAN.md` M18: "A2A-as-coordination-skill (proposed)" — ⬜ not started.
- Nothing under `server/`: no Agent Card, no `/.well-known/`, no task API, no client.

So this note is the first design of the thing itself.

## Why now

A2A is a Linux Foundation standard (spec 1.0, 150+ organisations); Google ADK, LangGraph, CrewAI
and kagent agents discover and call each other through it. SwarmKit topologies are reachable
today as **MCP tools** (`POST /mcp`, one tool per topology) — the tool-level integration. The
agent-level integration is missing, and it is the one that lets a SwarmKit swarm sit *inside*
someone else's agent graph, or orchestrate their agents, without either side adopting the other's
framework. For a self-hosted runtime with no distribution channel, being callable from every
other framework is the adoption path.

## Goal

1. **Server:** `swarmkit serve` publishes an A2A Agent Card and answers the A2A task API for each
   topology, mapped onto the existing job model — so a run started over A2A is governed, audited,
   gated and resumable exactly like one started over `POST /run`.
2. **Client:** a remote A2A agent is a skill (`implementation.type: a2a_agent`), so an agent in a
   topology can hand work to an ADK/LangGraph/kagent agent through the same permission seam every
   other skill goes through.

## Non-goals

- **Not a new execution path.** A2A is a *transport* onto jobs. No second job store, no second
  gate mechanism, no second audit trail. If a feature would need those, it is not this note.
- **Not gRPC or push notifications in the first cut.** JSON-RPC 2.0 over HTTP + SSE streaming is
  what the reference implementations speak; gRPC and `tasks/pushNotificationConfig/*` return
  `UnsupportedOperationError` until asked for.
- **Not agent-card signing** in the first cut. The card is served over the instance's own auth;
  signatures follow when a fleet needs cross-org trust.
- **Not replacing `peer-handoff`.** It stays the text-packaging skill; a remote agent is a
  different thing (a *capability*, category `capability`), not a coordination skill.
- **Not a directory/registry.** Discovery is the well-known URL per instance; the fleet control
  plane may aggregate cards later (it already reads `/capabilities`).

## API shape

### Server: the Agent Card

`GET /.well-known/agent-card.json` on a serve instance returns one card for the **instance**, with
one A2A *skill* per topology — the same shape the MCP mount uses (one tool per topology). A2A's
`skills[]` are advertised capabilities, which maps exactly; A2A does not require one card per
agent, and one card per topology at `/a2a/{topology}/.well-known/agent-card.json` is offered too
for clients that want a single-purpose agent.

```json
{
  "name": "swarmkit — my-workspace",
  "description": "SwarmKit serve instance; each skill is a governed topology run.",
  "url": "https://host:8000/a2a",
  "version": "1.221.0",
  "provider": { "organization": "…from workspace.yaml server.identity…" },
  "capabilities": { "streaming": true, "pushNotifications": false, "extendedAgentCard": false },
  "securitySchemes": { "bearer": { "type": "http", "scheme": "bearer" } },
  "security": [{ "bearer": [] }],
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/plain", "application/json"],
  "skills": [
    {
      "id": "code-review",
      "name": "code-review",
      "description": "…topology description…",
      "tags": ["swarmkit", "topology"],
      "examples": ["Review PR #49 on delivstat/swarmkit"]
    }
  ]
}
```

`securitySchemes` is derived from the instance's configured auth provider (`none` → no scheme;
`api_key` → bearer; `jwt` → bearer with the issuer's URL). The card says what the server already
enforces; it never widens it.

### Server: the task API

`POST /a2a` (JSON-RPC 2.0). The skill id in the message's metadata — or `/a2a/{topology}` for
the per-topology endpoint — selects the topology. Mapping, and it is a mapping, not new logic:

| A2A | SwarmKit |
|---|---|
| `message/send` | `POST /run/{topology}` with `input` = concatenated text parts, `attachments` = file parts (data-URI or workspace path; the 20 MB and image-only rules of `attachments.md` apply unchanged), `correlation_id` = `contextId`, label `a2a.task_id` = the task id; returns the Task |
| `message/stream` | the same submit, then `GET /jobs/{id}/stream` re-emitted as A2A `TaskStatusUpdateEvent` / `TaskArtifactUpdateEvent` over SSE |
| `tasks/get` | `GET /jobs/{id}` (+ the artifact when completed, as a `data`/`text` part) |
| `tasks/list` | `GET /jobs/history` filtered to jobs carrying an `a2a.task_id` label |
| `tasks/cancel` | `POST /jobs/{id}/stop` — cooperative, at the next agent boundary; the task reports `canceled` only when the job reports `stopped` |
| `tasks/subscribe` | `GET /jobs/{id}/stream` from the current position |
| `tasks/pushNotificationConfig/*` | `UnsupportedOperationError` (first cut) |
| `agent/getExtendedAgentCard` | the same card (no extended card yet) |

Task state, from job status:

| job | A2A task state |
|---|---|
| `pending` | `submitted` |
| `running` | `working` |
| `deferred` (parked on a human gate) | **`input-required`** — see below |
| `completed` | `completed`, artifact attached |
| `failed` | `failed` |
| `stopped` | `canceled` |
| refused at submit (422: bad attachment, unknown topology, auth) | `rejected` / JSON-RPC error |

**The one place the mapping is not mechanical: `input-required`.** A2A's `input-required` means
"the *caller* can supply what is needed". A SwarmKit run parks on a **human** gate — a funnel's
`approve` layer, a relay approval, a multi-party role task — and the human is *not* the A2A
caller; approval scopes are structurally un-grantable to agents (§8.7), and an A2A client is an
agent. So a deferred run is reported as `input-required` with a status message that says *what*
it is waiting for and *who* can resolve it (`GET /gates/{gate_id}` is linked in the message
metadata), and a follow-up `message/send` on that task from the A2A caller does **not** resolve
the gate — it is refused with `UnsupportedOperationError` and the gate's URL. The human resolves
it through `swarmkit review` / the portal / `POST /review/{id}/…` as today; the task then moves to
`working` and the caller's `tasks/subscribe` sees it. This is the whole reason A2A rides on the
job model rather than beside it: the gate cannot be talked past over a new transport.

The exception is a harness **input request** (§6.3, "what do you want?") — that *is* a question
the caller may be able to answer. It is reported as `input-required` too; a `message/send` on the
task with a text part is routed to `POST /review/{id}/answer` **only if** the pending item is an
`input_request` and the A2A principal's scopes include `serve:review:answer`. Approvals never.

Every A2A call is audited as the same `run.*` / `gate.*` events with `actor` = the authenticated
A2A principal and `transport: a2a` in the event's metadata. No new event kinds.

### Client: a remote A2A agent as a skill

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: adk-research-agent
  name: Research agent (ADK, over A2A)
  description: Delegates a research question to the platform team's ADK agent.
category: capability
implementation:
  type: a2a_agent
  card_url: https://research.internal/.well-known/agent-card.json   # discovery
  skill_id: deep-research                                           # which A2A skill; omit = the card's first
  credentials_ref: research-agent                                   # workspace credential, resolved by the credential service
  timeout_s: 600
  on_input_required: fail | answer_with_prompt   # default fail: a remote agent asking for input is a run failure, not a silent hang
effects: { deep-research: read }   # the same `effects` map readonly tiers already require
```

The compiler turns it into a tool the agent can call: arguments `{input: str, context?: dict}`,
result the task's final artifact as text/JSON. It goes through the **same permission seam** as an
MCP tool or a command — `permission` tiers, `requires:` prerequisites, `readonly` effects, audit
of the call — because it is a skill (§12: skills are the only capability extension primitive). The
card is fetched at resolve time (cached), so `swarmkit validate` reports an unreachable card or a
missing skill id before a run does.

Streaming from the remote agent is consumed but not re-streamed into the caller's tool result in
the first cut (the tool call returns when the remote task reaches a terminal state).

### Schema

`skill.schema.json` gains an `a2a_agent` implementation variant (`card_url` required, `skill_id`,
`credentials_ref`, `timeout_s`, `on_input_required`). `docs/notes/schema-change-discipline.md`
applies: schema + Python + TS validators + fixtures (valid + invalid) + codegen drift check.

### Config

```yaml
# workspace.yaml
server:
  a2a:
    enabled: true            # default false — one more surface is opted into, like mcp.enabled
    identity:
      organization: delivstat
      url: https://delivstat.com
```

`swarmkit system` reports whether A2A is mounted; `/capabilities` lists `a2a: true` so the fleet
panel can show it.

## Test plan

- **Unit — card:** built from a resolved workspace; one skill per topology; `securitySchemes`
  follows the auth provider; disabled → 404 at the well-known path.
- **Unit — mapping:** every job status maps to exactly one task state (a parametrised test over
  the `Literal`, so a new job status fails here); a deferred job is `input-required` with the gate
  URL in metadata; a `message/send` on a deferred task is refused and the gate is untouched
  (assert on the review queue).
- **Unit — JSON-RPC:** each method, malformed request → -32600, unknown method → -32601, unknown
  task → `TaskNotFoundError`, unsupported → `UnsupportedOperationError`.
- **Integration — server:** submit over A2A on the hello-swarm workspace with `provider: mock`;
  `message/stream` yields status events ending in `completed` with the artifact; `tasks/cancel`
  ends in `canceled`; the audit log has the same events as a `POST /run` of the same topology,
  plus `transport: a2a`.
- **Integration — client:** a fake A2A server (the same routes, in-process) behind an `a2a_agent`
  skill; the agent's tool call returns the remote artifact; `readonly` tier refuses it when
  `effects` says write; `on_input_required: fail` fails the run with the reason; unreachable card
  is a `validate` error.
- **Conformance:** the official A2A test client (`a2a-sdk` Python) discovers the card, sends a
  message, streams, gets, cancels — run in CI against the mock-provider workspace.
- **Schema:** fixtures for the new implementation type, valid and invalid; TS + Python validators
  agree.

## Demo plan

- `just demo-a2a`: starts serve with `a2a.enabled`, prints the card, runs the official A2A CLI
  client against `code-review`, shows the run in `swarmkit trace`, then runs a topology whose
  agent calls the same instance back over A2A as a skill (self-loop is the cheapest cross-agent
  demo). A recorded transcript in the PR.
- The stretch demo for the blog: an **ADK** agent (`google-adk`, a few lines) calling a SwarmKit
  topology it discovered from the card — the sentence "an ADK agent just ran a governed SwarmKit
  swarm and waited on its human gate" is the whole post.

## Slices

1. Card + `message/send` + `tasks/get` + `tasks/cancel` (server). Conformance client passes the
   non-streaming subset.
2. `message/stream` + `tasks/subscribe` over SSE; `input-required` mapping with the gate rule.
3. `a2a_agent` skill (client) + schema + validate-time card check.
4. Fleet: `/capabilities` reports A2A; the panel lists cards. Card signing when a real need appears.

## Open questions

- **One card per instance vs per topology.** Both are offered above; if the ecosystem's clients
  turn out to assume one card = one agent, drop the instance card.
- **`contextId` ↔ `correlation_id`.** A2A's `contextId` groups turns of one conversation; SwarmKit's
  `correlation_id` groups runs of one ticket. Same idea, but a multi-turn A2A conversation on one
  `contextId` should probably map to a SwarmKit *conversation* (`POST /conversations`) rather than
  a fresh run per message. First cut: one run per message; revisit with a real multi-turn caller.
- **Which artifact is "the" A2A artifact** when a run saves several (`--save-artifact`): the root
  agent's final output as the task artifact, others listed in metadata by ref.
- **§21:** does A2A-as-transport change the sandboxing question? No — the callee is still a local
  run; the caller is a principal. Noted for the next design revision.
