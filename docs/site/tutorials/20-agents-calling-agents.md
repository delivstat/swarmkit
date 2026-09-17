# Level 20: Agents calling agents

Another agent as a skill — a topology in this workspace, or a remote agent over **A2A** — and this
instance as an A2A agent to the outside.

## What you'll learn

- `implementation.type: agent`: one skill type, two resolutions (`topology:` / `card_url:`)
- Local child runs: attribution, correlation, shared MCP servers, the depth cap
- `pack:workspace` — "the supervisor may run any topology here", one line
- `on_unanswerable: agent | relay | abort` — who answers the other agent's questions
- Serving: `server.a2a.enabled`, the Agent Card, the task API as a transport onto jobs
- Discovery as an authoring act: the portal's "Add remote agent"
- Harness nodes get the same skill through the gateway

## The idea

Every topology `swarmkit serve` hosts is an A2A agent to the outside — it is on the card. Inside the
workspace nothing is reachable until it is granted, the same rule MCP servers and command packs follow.
Calling another agent is therefore a **skill**, not a new mechanism: same permission seam, same
`requires:`, same audit. And a human gate on the other side is never the calling agent's to resolve,
whatever the policy says — approval scopes are un-grantable to agents ([A2A interop](../design-notes/a2a-interop.md)).

## Build it

### 1. A local child

```yaml
# skills/ask-research.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata: { id: ask-research, name: Research agent, description: Delegates a question to deep-research. }
category: capability
implementation:
  type: agent
  topology: deep-research        # a topology in this workspace
  on_unanswerable: relay
  permission: cautious
  effects: read
provenance: { authored_by: human, version: 1.0.0 }
```

The target runs **in-process as a child of the caller's run**: its own run id and trace, a job row
with `parent_job_id` and `source: agent`, the caller's correlation, the same MCP servers (never
closed by the child). A missing target fails the workspace load. Depth is capped at 3.

Or grant every topology at once:

```yaml
agents:
  root:
    skills: [pack:workspace]     # topology-<name> for every topology, now and later
```

### 2. A remote agent

```yaml
implementation:
  type: agent
  card_url: https://legal.example.com/.well-known/agent-card.json
  skill_id: contract-review           # one of the card's skills; omitted = the first
  credentials_ref: legal-agent        # a workspace `credentials` entry, sent as a bearer
  timeout_s: 1800
  on_unanswerable: agent              # the calling agent may answer a clarification, bounded
  max_agent_answers: 2
```

The card is fetched on first use; the call is `message/send` with our run id as the A2A `contextId`
(so two instances' records join on it), then `tasks/get` until the task ends or asks for input.

When the remote agent asks a question:

| policy | who answers |
|---|---|
| `agent` (default) | the tool result is `{"status": "input_required", "task_id", "question"}`; the agent calls again with `{task_id, answer}`; audited as `executor.input_response` with `responder: agent:<id>`; after `max_agent_answers`, a person |
| `relay` | a person, through the same review-queue item and bounded wait a harness question uses |
| `abort` | the call fails with the question; the remote task is cancelled |

A **human gate** on the far side comes back as `kind: human_gate` under every policy.

Rather than writing that file: **Connections → Add remote agent** in the portal takes the card URL,
shows the agent's skills, and writes the skill for the one you pick.

### 3. Serve your topologies as agents

```yaml
# workspace.yaml
server:
  a2a:
    enabled: true
    identity: { name: Review desk, organization: Example Org }
```

```bash
curl -s http://127.0.0.1:8000/.well-known/agent-card.json | jq '.skills[].id'
curl -s http://127.0.0.1:8000/a2a -d '{"jsonrpc":"2.0","id":1,"method":"message/send",
  "params":{"message":{"kind":"message","role":"user","messageId":"m1",
  "parts":[{"kind":"text","text":"Greet engineers"}],"metadata":{"skill":"hello"}}}}'
```

`message/send` is `POST /run`, `tasks/get` is `GET /jobs/{id}`, `tasks/cancel` is the cooperative
stop, `message/stream` is the job stream as A2A events. A run parked on a gate reports
`input-required` with the gate URL; a follow-up message on it is refused — a person resolves it.

## Run it

```bash
just demo-a2a-server     # card, message/send, tasks/get|list, the refusal, message/stream
just demo-agent-skill    # local child run; remote call to a second serve instance; the gateway; a missing target
```

Both run on the mock provider with no network. The second one ends with a harness-style call: the
same skill offered through the governed MCP gateway as `agent__ask-hello`, with the child run landing
under the harness's run.

## What happened

- The child was a job of its own, nested under the caller — `swarmkit trace` and the jobs page show
  it; `GET /jobs/history?correlation_id=…` groups them.
- The remote call was one A2A task; the remote's jobs table shows `source: a2a` and your run id as
  its correlation. What the remote agent did internally is not visible unless it reports it — the
  trace says "unknown", not a guess.
- Nothing was granted by enabling A2A or by adding a remote agent: a topology names the skill, or
  holds `pack:workspace`, to reach it.

## Learn more

- [A2A interop](../design-notes/a2a-interop.md) — the whole design, and what is still open
- [Skills reference](../reference/skills.md) — the `agent` backing and `pack:workspace`
- [Serve mode](../reference/serve.md) — the A2A endpoint · [Connections](../reference/connections.md) — remote agents
- [Level 17](17-harness-executors.md) — how a harness node reaches the same skill
