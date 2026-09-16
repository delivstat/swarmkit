---
title: A2A interop — every topology is an A2A agent, and a remote A2A agent is a skill
description: Serve publishes an Agent Card and the A2A task API for each topology, mapped onto the existing job model so governance is untouched; an `agent` skill calls another topology (in-process, a child job) or a remote A2A agent (over the wire) through the same permission seam, streamed and audited like every other skill. The first real A2A work — earlier notes named it but nothing shipped.
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
2. **Client:** another agent is a skill — `implementation.type: agent` — resolving either to a
   **topology in the same workspace** (run in-process as a child job; no wire) or to a **remote A2A
   agent** (by card URL). Either way the calling agent reaches it through the same permission seam
   every other skill goes through, and the call is streamed and audited like every other skill.
   This is also the "sub-swarm as a skill" M18 proposed and never built: it falls out of the
   remote form, because both are "start a run, wait, hand back the artifact".

## Non-goals

- **Not a new execution path.** A2A is a *transport* onto jobs. No second job store, no second
  gate mechanism, no second audit trail. If a feature would need those, it is not this note.
- **Not gRPC or push notifications in the first cut.** JSON-RPC 2.0 over HTTP + SSE streaming is
  what the reference implementations speak; gRPC and `tasks/pushNotificationConfig/*` return
  `UnsupportedOperationError` until asked for.
- **Not agent-card signing** in the first cut. The card is served over the instance's own auth;
  signatures follow when a fleet needs cross-org trust.
- **Not replacing `peer-handoff`.** It stays the text-packaging skill; another agent is a
  different thing (a *capability*, category `capability`), not a coordination skill.
- **Not runtime discovery.** No topology scans a network, a registry or a directory for agents
  at run time — see "Discovery" below for why and for what discovery *is*.
- **Not automatic reachability.** Every topology in a workspace is *advertised* on the card, but
  an agent can only call the ones it has been *granted* as skills — see "Every topology is
  callable; none is granted by default".

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

### Client: another agent as a skill (`implementation.type: agent`)

One skill type, two resolutions. Local when the target is a topology in this workspace; remote
when it is a card URL.

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: research
  name: Research agent
  description: Delegates a research question to the research agent.
category: capability
implementation:
  type: agent
  # exactly one of:
  topology: deep-research                                          # same workspace → child job, no wire
  card_url: https://research.internal/.well-known/agent-card.json  # elsewhere → A2A transport
  skill_id: deep-research          # remote only: which A2A skill; omit = the card's first
  credentials_ref: research-agent  # remote only: workspace credential, resolved by the credential service
  timeout_s: 600
  on_input_required: agent         # agent | relay | fail — see below
  max_agent_answers: 2
effects: { research: read }        # the same `effects` map readonly tiers already require
```

The compiler turns it into a tool the agent can call: arguments `{input: str, context?: dict}`
(and `{task_id, answer}` to answer a question — below), result the task's final artifact as
text/JSON. It goes through the **same permission seam** as an MCP tool or a command — `permission`
tiers, `requires:` prerequisites, `readonly` effects, audit of the call — because it is a skill
(§12). Nothing about "it is an agent on the other end" changes what the caller is allowed to do.

**Local form.** `topology:` starts the target as a **child job** (`parent_job_id` = the caller's
job, same `correlation_id`), in-process, through the same job manager `POST /run` uses. It is not
a network call to yourself: no bearer token, no serialisation, and `swarmkit trace` shows the
child's agents, tools and tokens nested under the tool call. A child that parks on a gate is
`input-required` to its caller exactly like a remote one.

**Remote form.** `card_url:` is fetched at **resolve time** (cached, with the card's `version`),
`skill_id` checked against `skills[]`, the security scheme read; `swarmkit validate` reports an
unreachable card or an unknown skill before a run does. At run time the call is `message/stream`
(falling back to `message/send` + `tasks/subscribe` when the card says `streaming: false`), with
the input as text parts and any attachments as file parts.

### Every topology is callable; none is granted by default

The instance card lists every topology, so every topology *is* an A2A agent to the outside. Inside
the workspace, nothing is reachable until it is granted: an agent's capability set is what its
topology declares, and if every topology were implicitly callable by every agent then
`validate --require`, `readonly` tiers, `requires:` and the audit's answer to "what could this
agent do" would all stop meaning anything. This is the same rule MCP servers already follow —
declared in `workspace.yaml` makes a server *available*; a skill makes a tool *granted*.

Granting is one line. `reference/` ships a generated **`pack:workspace`** — one `agent` skill per
topology in the workspace, regenerated by `validate` — so "the supervisor may run any topology
here" is `skills: [pack:workspace]`, the way `pack:git` grants a bundle's read skills today, and
`validate` still knows exactly what the agent can reach.

### When the other agent asks a question (`on_input_required`)

A remote agent (or a local child) can go `input-required`. This is the harness input request
(§6.3) arriving over a wire, and it uses the **same machinery**, not a new one. Who answers is a
policy on the skill, mirroring the permission tiers:

| policy | behaviour |
|---|---|
| `agent` (default) | The question comes back to the **calling agent** as the tool result — `{"status": "input_required", "task_id": …, "question": …}`. The agent framed the task, so a clarification ("which repo?", "PDF or markdown?") is its call: it answers by calling the skill again with `{task_id, answer}`. Each answer is audited as `answered_by: agent:<id>` and counts against the turn budget. After `max_agent_answers`, or when the agent decides it is not its call (the existing `escalate-to-human` coordination skill, or a `relay` result), it escalates. |
| `relay` | Always a human, never the agent. |
| `fail` | The tool call fails with the question as the reason — for unattended batch runs. |

**Escalation to a human** parks the caller's run — checkpoint, job `deferred`, nothing resident
(a remote agent may take hours; holding a process open for it is the mistake defer-and-resume
exists to avoid) — and files an **`input_request`** review item carrying the question, the task
id, which skill asked, and the context. It lands in the same inbox as a harness question:
`swarmkit review list`, `POST /review/{id}/answer`, the portal, the fleet cockpit. The answer is
sent as `message/send` on the remote task; the run resumes from its checkpoint with the tool
result. Rejecting the item cancels the remote task and the tool call fails with the reason.

**What the agent may never answer, whatever the policy.** The lines that already exist for tools:
on a `strict` tier the answer is a write-shaped act → relay. A question that is really an
*approval* ("may I delete the branch?") is a governance decision, not a clarification → a human;
detected the honest way — the answer would grant an effect the skill's `effects` map marks as a
write. And an answer is a text part, never a credential or a scope, so a remote agent cannot
obtain either by asking. Clarifications go to the agent, bounded and audited; decisions go to a
human, structurally — the same distinction §6.2/§6.3 draw for harnesses.

This makes the rule symmetric across the wire: a harness asking "what do you want?", a remote
agent asking the same, and **our own run** parked on a gate while *serving* an A2A call all appear
as `input-required` / `input_request`, all resolved through the review queue, all audited.

### Streamed and audited like every other skill

- **Audit.** The call is the existing `tool.call` event — arguments, result size, duration, actor,
  the run's correlation — with the A2A specifics in metadata: card URL, remote skill id, task id,
  `contextId`, final task state. Every `input-required` round is its own event (`answered_by`
  agent id or human identity); a cancel is recorded. The local form is a child job, so its whole
  interior is in the trace under `parent_job_id`. No new event kinds.
- **Streaming.** A2A's `TaskStatusUpdateEvent` / `TaskArtifactUpdateEvent` from `message/stream`
  are forwarded into the caller's `GET /jobs/{id}/stream` as progress events under the tool-call
  span — the run view and `swarmkit logs` show "remote agent: working… artifact… completed" live,
  as a harness node's JSONL events do today. Local children stream natively. Push notifications
  are not needed for this.
- **The limit, stated.** We record what crosses the wire. A remote agent's own tool calls, tokens
  and cost are not visible unless it reports them in task metadata (A2A does not require it), so
  the trace shows the remote call as one node with duration and result and cost "unknown" unless
  reported — the same honesty the trace applies to a harness's internal model calls. Two SwarmKit
  instances talking A2A can both report to the fleet control plane, which can join them by
  `contextId` — that is where "one run rendered across instances" earns its name.

### Discovery

Two levels; only the first belongs in the runtime.

1. **Resolving a declared agent** — the skill names a `card_url`; the runtime fetches the card
   (RFC 8615 well-known URL) at resolve time, checks the skill id, reads the security scheme,
   caches it. That is discovery in the A2A sense, and it is in scope.
2. **Finding agents nobody declared** — scanning a network, a registry or a directory at run
   time — is **not** something a topology does. A swarm that can discover and call arbitrary
   agents has a capability set unknown until it runs, which defeats `validate --require`,
   `readonly` effects and the premise that the artifact says what it can do. Discovering a *new*
   agent is a **human authoring act**: the portal's Connections page (where MCP OAuth servers are
   added today) gets "Add an A2A agent by card URL" → shows the card's skills → the person picks
   one → a skill file is written, the way a `swarmkit-skills` bundle is imported. The fleet
   control plane may aggregate its own instances' cards so cross-instance delegation is a
   pick-list, not a scan.

### Schema

`skill.schema.json` gains an `agent` implementation variant: exactly one of `topology` /
`card_url`; `skill_id`, `credentials_ref`, `timeout_s`, `on_input_required` (`agent | relay |
fail`), `max_agent_answers`. `docs/notes/schema-change-discipline.md` applies: schema + Python +
TS validators + fixtures (valid + invalid) + codegen drift check. The composer's skill form is
schema-driven and gains the variant with the schema change (as `command` did).

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

### Portal

Deliberately small. System page: one row ("A2A: enabled, card at …"). Review inbox: **no new
kind** — a run that arrived over A2A and parked on a gate appears as any deferred run does; the
run card shows `transport: a2a` and the caller principal, which the audit event already carries.
Runs/trace: a job; a local child appears nested. Workspace config: `server.a2a.enabled` through
the existing `PUT /api/workspace/config/{section}` path. Skill editor: the `agent` variant,
schema-driven. Connections page: "Add an A2A agent by card URL" (Discovery §2). Fleet panel: an
A2A capability chip from `/capabilities`.

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
- **Integration — client, remote:** a fake A2A server (the same routes, in-process) behind an
  `agent` skill; the agent's tool call returns the remote artifact; `readonly` tier refuses it when
  `effects` says write; unreachable card or unknown skill id is a `validate` error; streamed
  status/artifact events appear in the caller's job stream under the tool-call span.
- **Integration — client, local:** `topology:` runs a child job with `parent_job_id`; the trace
  nests it; a child parked on a funnel gate surfaces as `input_required` to the caller; the
  `pack:workspace` skill pack lists every topology and regenerates on `validate`.
- **Input requests:** `agent` policy — the question reaches the calling agent, its answer is sent
  on the task and audited as `answered_by: agent:<id>`, the budget caps it and the next round
  escalates; `relay` — the run parks `deferred`, an `input_request` item is filed, a human answer
  resumes it, a rejection cancels the remote task; `fail` — the tool fails with the question;
  a `strict` tier or a write-shaped answer always escalates whatever the policy.
- **Audit fields:** the `tool.call` event for an `agent` skill carries card URL, skill id, task
  id, `contextId`, final state; each input round is an event; no new event kinds (a test over
  the audit schema's kind list).
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
3. `agent` skill: local form (child job) first, then remote (card resolution, streaming),
   `on_input_required` with the review-queue relay; schema; `pack:workspace`.
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
