# AG-UI protocol surface on `swarmkit serve`

**Status:** draft, implementation PR follows.
**Issue:** #1016. Phase 1 of #1018 (the `dots` reference app epic).
**Related:** `design/details/dot-app.md` (DOT's custom SSE path we don't replace), `design/details/skill-requires-credentials.md`, `_routes_jobs.py` (existing `/run/{topology}` SSE machinery we build on).

## Goal

Expose the [AG-UI protocol](https://docs.ag-ui.com/) as a native surface on `swarmkit serve`, so any CopilotKit-flavoured frontend talks to a SwarmKit backend without a bespoke adapter. One protocol surface; every AG-UI client (CopilotKit React/Vue/iOS/Android, LangGraph UI, …) works out of the box.

This is **phase 1 of #1018** (the `dots` reference app) and lands independently as a protocol capability. Nothing else blocks on it; nothing it ships blocks on anything.

## Non-goals

- **Not replacing** DOT's custom SSE path at `/run/{topology}`. DOT (singular, card-stack personal workflow) keeps its direct SSE surface; AG-UI is an additional surface, not a swap. See `design/details/dot-app.md` §Non-goals.
- **Not shipping a SwarmKit UI library.** The whole point is to not do that — AG-UI lets consumers pick their own frontend.
- **Not implementing full-fidelity shared-state semantics in v1.** We start with the subset that matters: lifecycle, messages, tool calls, subagents, interrupts. State snapshot/delta events are v2.
- **Not covering the HTTP+Protobuf binary binding.** HTTP+SSE only in v1. Protobuf is a performance optimisation worth adding later if frontend demand materialises.
- **Not shipping a Python client.** We ship the server; clients come from the ag-ui ecosystem.

## What AG-UI is (the subset we care about)

Open protocol defining eight event families carried over HTTP+SSE between a client (frontend) and a server (agent backend). Event vocabulary we implement:

| Family | Events | Our source |
|---|---|---|
| **Lifecycle** | `RunStarted`, `RunFinished`, `RunError`, `StepStarted`, `StepFinished` | Topology run start/finish/error; archetype node enter/exit |
| **Messages** | `TextMessageStart`, `TextMessageContent`, `TextMessageEnd`, `TextMessageChunk` | LLM assistant text output (streamed from `ModelProvider`) |
| **Tool calls** | `ToolCallStart`, `ToolCallArgs`, `ToolCallEnd`, `ToolCallResult`, `ToolCallChunk` | MCP tool invocations through the governed path |
| **Subagents** | `SubagentStarted`, `SubagentFinished`, `SubagentError` | Agent-skill child runs (another topology as a tool) |
| **Reasoning** | `ReasoningStart`, `ReasoningMessageContent`, `ReasoningEnd` | Model reasoning traces where providers expose them (OpenAI o1, Anthropic extended thinking) |

Carried on every event: `type`, `timestamp`, `metadata` (open-key), optionally `subagentRunId`.

Events we **do not** emit in v1 (and why):

| Family | Why skipped |
|---|---|
| `State` (snapshot/delta via RFC 6902) | Needs model of agent state the UI mirrors; v2 after we see demand |
| `Activity` (snapshot/delta) | Overlap with our audit; v2 |
| `Raw` / `Custom` | Only when consumers ask |

Interrupts (HITL) are a `RunFinished` with `outcome: {type: "interrupt", interrupts: [...]}`. See §Interrupts.

## Endpoint shape

Single run endpoint plus a resume endpoint.

### `POST /api/ag-ui/run`

Request body: `RunAgentInput` (AG-UI's canonical type).

```jsonc
{
  "threadId": "optional-ulid",       // caller-supplied; a thread groups runs
  "runId": "optional-ulid",          // caller-supplied; one per logical run
  "messages": [                      // thread history including the new user message
    {"role": "user", "content": "..."}
  ],
  "tools": [],                       // frontend-exposed tools the agent can call (v1: ignored, pass-through)
  "context": {                       // free-form metadata (user id, locale, feature flags)
    "topology": "morning-brief"      // SwarmKit extension: which topology to run
  },
  "state": {}                        // frontend-held state (v1: ignored, logged)
}
```

Response: `text/event-stream` carrying the event families above. One response = one run. The stream closes on `RunFinished` or `RunError`.

Headers:

- Request: `Content-Type: application/json`, `Accept: text/event-stream`
- Auth: `Authorization: Bearer <token>` + `X-Owner: <owner>` (same as `/api/mcp/{server_id}/invoke` and `/run/{topology}`)
- Response: `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `Connection: keep-alive`

### `POST /api/ag-ui/run/resume`

Resume a run previously suspended by an interrupt (HITL approval).

```jsonc
{
  "threadId": "...",
  "runId": "...",                     // the suspended run's id
  "interrupts": [                     // one entry per interrupt the client is resolving
    {"interruptId": "...", "resolution": {"approved": true, "note": "..."}}
  ]
}
```

Response: `text/event-stream`, same format. Continues the run from the gate.

### Session grouping — no new concept

AG-UI uses `threadId` to group runs. We treat `threadId` as a correlation id attached to the audit log (the existing correlation-id story). No new storage, no new session table. A thread with 10 runs is 10 rows in the audit log with the same `correlation_id`.

## Topology selection

A SwarmKit backend hosts many topologies; AG-UI's `RunAgentInput` has no first-class topology field. We use `context.topology` as the SwarmKit-specific hint:

- `context.topology: "morning-brief"` → run that topology
- Missing → 400 with "topology required in context.topology"
- Unknown → 404 "topology_not_found: ..."

This extension is namespaced-safe (any field in `context` is implicitly consumer-defined per AG-UI).

## Event mapping — concrete

### SwarmKit run start → AG-UI RunStarted

```jsonc
{
  "type": "RunStarted",
  "timestamp": 1727905200000,
  "runId": "run-01HK...",
  "threadId": "thread-01HJ...",
  "input": {"topology": "morning-brief"}
}
```

### Archetype node enters → AG-UI StepStarted

```jsonc
{"type": "StepStarted", "timestamp": ..., "stepName": "context-aggregator"}
```

### LLM assistant text → AG-UI TextMessage*

```jsonc
{"type": "TextMessageStart", "messageId": "msg-01HK...", "role": "assistant"}
{"type": "TextMessageContent", "messageId": "msg-01HK...", "delta": "Here are "}
{"type": "TextMessageContent", "messageId": "msg-01HK...", "delta": "your five..."}
{"type": "TextMessageEnd", "messageId": "msg-01HK..."}
```

### MCP tool call → AG-UI ToolCall*

```jsonc
{"type": "ToolCallStart", "toolCallId": "tc-01HK...", "toolCallName": "gmail.search_threads"}
{"type": "ToolCallArgs", "toolCallId": "tc-01HK...", "delta": "{\"query\":"}
{"type": "ToolCallArgs", "toolCallId": "tc-01HK...", "delta": "\"is:unread\"}"}
{"type": "ToolCallEnd", "toolCallId": "tc-01HK..."}
{"type": "ToolCallResult", "toolCallId": "tc-01HK...", "messageId": "msg-...", "content": "[{...}]"}
```

### Child topology (agent skill) → AG-UI Subagent*

Agent-skill backing runs a topology as a child job. SwarmKit's existing `agent` skill implementation emits this naturally.

```jsonc
{"type": "SubagentStarted", "subagentRunId": "run-01HK-child...", "name": "handle-item", "parentMessageId": "msg-..."}
{"type": "SubagentFinished", "subagentRunId": "run-01HK-child...", "outcome": {"type": "success"}, "result": {...}}
```

### HITL approval gate → RunFinished with interrupt

The run suspends; the client resolves and resumes via `POST /api/ag-ui/run/resume`.

```jsonc
{
  "type": "RunFinished",
  "runId": "run-01HK...",
  "outcome": {
    "type": "interrupt",
    "interrupts": [{
      "interruptId": "gate-01HK...",
      "kind": "approval",
      "detail": {
        "scope": "approvals:resolve",
        "artifact": {"kind": "email_draft", "to": "...", "subject": "...", "body": "..."},
        "prompt": "Send this reply?"
      }
    }]
  }
}
```

### Final success → RunFinished with success

```jsonc
{"type": "RunFinished", "runId": "...", "outcome": {"type": "success"}, "result": {...}}
```

### Failure → RunError

```jsonc
{"type": "RunError", "runId": "...", "message": "...", "code": "..."}
```

## Governance invariants (unchanged)

Every AG-UI call lands at the same service-layer boundary as `/run/{topology}`:

- Auth middleware runs first; identity resolved via `_principal.py`
- MCP tool calls flow through `governed_mcp_call` — same scope checks, same audit entry
- HITL gates use the same `approvals:resolve` scope; the `resume` endpoint hands the resolution to the normal gate machinery (nothing bespoke)
- Audit log receives the same events it would from a `/run/{topology}` run — AG-UI is a projection on the audit, not a parallel store

Invariant: **"an AG-UI run and a `/run/{topology}` run produce byte-identical audit output."** CI test enforces it.

## Implementation plan

### Modules

- `packages/runtime/src/swarmkit_runtime/server/_routes_ag_ui.py` — the two routes, event mapping
- `packages/runtime/src/swarmkit_runtime/server/_ag_ui_events.py` — the event-type dataclasses + JSON serialisation, mapping from SwarmKit internal events

### Reuse

- `_routes_jobs.py`'s SSE machinery (streaming, keep-alive, early-close handling)
- `governance/*` for approval gates (resume path)
- `mcp/_governed.py` for tool calls (nothing to change — the AG-UI route observes the governed call just like `/run` does)
- `_principal.py` for auth

### New

- `AgUiEventStream` adapter that subscribes to a running topology's internal event bus and translates each event into AG-UI vocabulary. Pure translation; no new state.
- `RunAgentInput` pydantic model
- Resume handler that reconnects the SSE stream to a parked run and pushes the resolution to the gate machinery

### Scope deferred

- State snapshot/delta events (RFC 6902) — v2
- Activity events — v2
- Protobuf binding — on demand
- Dedicated AG-UI auth header handling — v1 uses the existing bearer+X-Owner path

## Test plan

- Unit: event-mapping table — for each SwarmKit internal event, assert the correct AG-UI event emits with the right payload shape
- Unit: `RunAgentInput` parser rejects missing topology / malformed messages
- Unit: resume handler rejects stale/unknown runIds; forwards resolution to the gate correctly
- Integration: full `POST /api/ag-ui/run` against a fixture workspace with a two-step topology → asserts the event sequence
- Integration: a topology with an approval gate → first POST returns interrupt-outcome Finished; `POST /api/ag-ui/run/resume` continues to success
- Integration: a topology with an MCP tool call → asserts ToolCall* events emit and the governance audit entry is unchanged
- Integration: the invariant test — same topology run via `/run/{topology}` and `/api/ag-ui/run` produces byte-identical audit entries

## Demo plan

Ships with the implementation PR as a self-contained Python script (`examples/ag-ui/demo.py`) plus a one-line convenience target:

1. Boot `swarmkit serve` against a fixture workspace with a trivial echo topology
2. A 60-line Python client (plain `httpx` + manual SSE parse — no SDK dep) POSTs a `RunAgentInput`, iterates events, prints them with human labels
3. Transcript shows RunStarted → TextMessage* → ToolCall* → RunFinished

Suitable for pasting into the PR body.

## Open questions

- **Q1 — ThreadId as audit correlation id.** AG-UI's `threadId` and SwarmKit's `correlation_id` serve the same purpose. We alias them. If a client provides neither, we generate a ULID and return it on the first `RunStarted`. *Proposed default:* yes, alias + ULID.
- **Q2 — Frontend tools (`RunAgentInput.tools`).** CopilotKit lets the frontend expose actions the agent can call. In v1 we log but don't honour this (the agent only sees skills granted by the topology). *Proposed default:* v1 ignores, v2 supports via a new `frontend_tool` skill backing.
- **Q3 — `messages` carrying history.** The client sends the full thread each time. For long threads this is wasteful. v1 accepts and ignores anything but the last user message; v2 reconciles against the audit log. *Proposed default:* v1 accepts, uses only the trailing user turn.
- **Q4 — Reasoning events.** Not every provider surfaces chain-of-thought as a separate stream. For providers that do (OpenAI reasoning models, Anthropic extended thinking), emit `Reasoning*` events; for others, elide. *Proposed default:* provider-adapter decides.
- **Q5 — Error semantics.** AG-UI's `RunError.code` field is open-ended. We adopt a short set: `topology_not_found`, `topology_resolve_failed`, `provider_error`, `tool_error`, `governance_denied`, `internal`. Documented in the handoff guide.
