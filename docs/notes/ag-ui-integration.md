# AG-UI integration guide — connecting a CopilotKit-flavoured frontend to `swarmkit serve`

> **Written for a Claude Code session with no prior context on this repo.** Read this top-to-bottom and you'll have everything you need to wire a new frontend (or migrate an existing app) to SwarmKit's AG-UI surface. No memory, no earlier conversations required.

**Status:** stub (populated as #1016 implementation progresses). Every claim here is grounded in a file path or shipped test. Gotchas accrue during build; do not skip the "Gotchas discovered during build" section.

---

## 1. One-paragraph positioning

SwarmKit's `swarmkit serve` speaks [AG-UI](https://docs.ag-ui.com/), the open HTTP+SSE protocol for agent↔frontend communication. Any client that speaks AG-UI — [CopilotKit](https://www.copilotkit.ai/) React/Vue/iOS/Android, LangGraph UI, or a 50-line `httpx` script — connects to a SwarmKit runtime with no bespoke adapter. The runtime handles governance, skills, audit, and multi-agent coordination; the frontend handles rendering. If you're building a chat-shaped app on SwarmKit, this is the surface. For non-chat workflows (dashboards, card stacks), use the direct `/run/{topology}` SSE path instead — see `reference/apps/dot/` for a worked non-chat example.

## 2. Decision tree — is my app chat-shaped?

Use AG-UI (and the `dots` pattern) when:

- Users interact with the agent conversationally (ask, delegate, approve)
- Each thread has persistent state the agent reads/writes over time
- There's an HITL approval surface (user approves outputs before they commit)
- Outputs vary in shape (status updates, forms, tables, drafts) and want generative-UI rendering

Use direct SSE (`/run/{topology}`) when:

- The UI is a dashboard, card stack, or table
- Interactions are click-driven, not question-driven
- Output shape is fixed per screen

Both can coexist. SwarmKit has both surfaces by design.

## 3. Minimum viable client

A copy-pasteable Python client that proves the round-trip. No SDK dependency; plain `httpx` + manual SSE parsing. Save as `demo.py`, run against a `swarmkit serve` on localhost.

```python
# TODO: populated by the implementation PR once /api/ag-ui/run ships.
# Shape: POST RunAgentInput → stream events; print each with a human label.
```

For a React client using CopilotKit's SDK:

```tsx
// TODO: populated by the implementation PR
```

## 4. Endpoint reference

Two routes. Both authenticate with `Authorization: Bearer <token>` + `X-Owner: <owner>` (same as every other SwarmKit route; see `_principal.py`).

### `POST /api/ag-ui/run`

Starts a run. Request body: `RunAgentInput` (AG-UI canonical type). SwarmKit extension: `context.topology` is required and names the topology to run.

Response: `text/event-stream`. One response = one run. Stream closes on `RunFinished` (success or interrupt) or `RunError`.

Full request/response examples will land here when the route ships.

### `POST /api/ag-ui/run/resume`

Resumes a run previously suspended by an HITL interrupt. Request body carries the `runId` and one or more interrupt resolutions. Response is another `text/event-stream` that continues the run from the gate.

Full examples will land here.

## 5. Event-vocabulary mapping — SwarmKit internal → AG-UI

| SwarmKit internal event | AG-UI event(s) | Payload fields |
|---|---|---|
| Topology run begins | `RunStarted` | `runId`, `threadId`, `input` |
| Archetype node enters | `StepStarted` | `stepName` (archetype id) |
| Archetype node exits | `StepFinished` | `stepName` |
| LLM text token stream | `TextMessageStart` → `TextMessageContent*` → `TextMessageEnd` | `messageId`, `role`, `delta` |
| MCP tool call starts | `ToolCallStart` | `toolCallId`, `toolCallName` |
| MCP tool call args stream | `ToolCallArgs*` | `toolCallId`, `delta` |
| MCP tool call ends | `ToolCallEnd` + `ToolCallResult` | `toolCallId`, `messageId`, `content` |
| `agent` skill → child topology | `SubagentStarted` → `SubagentFinished`/`SubagentError` | `subagentRunId`, `name`, `outcome` |
| HITL approval gate hit | `RunFinished` with `outcome: {type: "interrupt", interrupts: [...]}` | per-interrupt `interruptId`, `kind`, `detail` |
| Final success | `RunFinished` with `outcome: {type: "success"}` | `result` |
| Failure | `RunError` | `message`, `code` |

Error code vocabulary used in `RunError.code`:

- `topology_not_found` — `context.topology` names an unknown topology (route also responds 404 upfront)
- `topology_resolve_failed` — workspace resolution threw during run
- `provider_error` — a `ModelProvider` call failed
- `tool_error` — an MCP tool call returned an error
- `governance_denied` — a scope check refused the operation
- `internal` — anything else; see audit log for the trace

Not emitted in v1 (and why): `State*`, `Activity*`, `Raw`, `Custom`. Reasoning events emit only when the provider surfaces chain-of-thought (OpenAI o1, Anthropic extended thinking).

## 6. Auth

AG-UI inherits SwarmKit's standard auth surface. There is **no** AG-UI-specific authentication:

- `Authorization: Bearer <token>` — provisioned per `design/details/mcp-oauth.md` and the auth CLI
- `X-Owner: <owner>` — identifies the owner whose per-user credentials resolve; absent means the caller's own identity (`_principal.py`)
- `Authorization` + `X-Owner` match → act as caller. `X-Owner` names a different owner → requires an admin scope (same discipline as `POST /api/mcp/{server_id}/invoke`)

Session cookies work too for browser clients; same cookie SwarmKit webui uses.

## 7. HITL approvals via AG-UI

The full sequence for a run that hits an approval gate:

1. Client posts `POST /api/ag-ui/run` with the topology. Stream opens.
2. Agent runs; emits text/tool-call events as normal.
3. Run hits an approval gate. Server emits:
   ```jsonc
   {
     "type": "RUN_FINISHED",
     "runId": "run-01HK...",
     "outcome": {
       "type": "interrupt",
       "interrupts": [{
         "interruptId": "gate-01HK...",
         "kind": "approval",
         "detail": {"artifact": {...}, "prompt": "..."}
       }]
     }
   }
   ```
   Stream closes.
4. Frontend renders the artifact; user clicks Approve or Reject.
5. Client posts `POST /api/ag-ui/run/resume` with `{threadId, runId, interrupts: [{interruptId, resolution: {approved: true}}]}`.
6. Server validates the caller has `approvals:resolve` scope (same gate the direct `/approvals/*/resolve` route uses — no AG-UI-specific policy).
7. Response is a new SSE stream continuing the run from the gate. Emits more events until `RunFinished{success}` or another interrupt.

A rejection resolves the gate to `approved: false`; the topology's post-approval branch decides what that means (usually: don't send the artifact, emit a terminal `RunFinished{success}` with a cancellation `result`).

## 8. Generative UI payloads

Some SwarmKit skills + archetypes return structured output (an email draft, a prep note, a retrieved thread) that a chat UI wants to render inline, not as prose. AG-UI carries these as `ToolCallResult` content whose shape the frontend can discriminate on.

Convention: the skill's `outputs` schema (declared in its YAML) is the payload shape. The frontend reads `tool_call.content` and renders by `kind` field. Example shapes used by the DOT reference workspace:

```jsonc
{"kind": "email_draft", "to": "...", "subject": "...", "body": "..."}
{"kind": "prep_note", "title": "...", "sections": [...]}
{"kind": "retrieved_thread", "title": "...", "summary": "...", "links": [...]}
{"kind": "acknowledgement", "summary": "..."}
```

The CopilotKit `<CopilotChat>` component can be configured to render a per-`kind` React component. Documentation on wiring that is the responsibility of the frontend side; see the `dots` reference app (phase 3 of #1018) when it ships.

## 9. Gotchas discovered during build

> ⚠ **This section is the point of this document.** Minimum three gotchas before #1016 merges. Backfilled by the implementation PR as unexpected behaviour surfaces.

### Gotcha 1 — SwarmKit's internal progress stream is string-based, not structured

The existing `/run/{topology}` endpoint streams progress through `langgraph_compiler._helpers.progress_listener`, which emits plain strings ("[assistant] thinking…", "calling get-weather", etc.). AG-UI's rich event vocabulary (`ToolCallStart`, `SubagentStarted`, `ReasoningEnd`) needs **structured** source events — fields like `toolCallId`, `toolCallName`, `delta` — that the string stream cannot faithfully carry.

**v1 scope cut:** AG-UI emits only the subset we can produce from the string stream:

- `RunStarted` — on job start
- `TextMessageStart` + `TextMessageContent` (one `delta` per progress line) + `TextMessageEnd` — single assistant message per run
- `RunFinished{success}` — on job completion
- `RunError` — on job failure

**Not emitted in v1 (deferred to v2):** `ToolCallStart`/`Args`/`End`/`Result`, `SubagentStarted`/`Finished`/`Error`, `Reasoning*`, `StepStarted`/`Finished`, `RunFinished{interrupt}` + `/resume`.

**Workaround for consumers today:** build against the lifecycle + messages subset. If your UX needs tool-call rendering or sub-agent attribution, wait for v2 or render against the `/jobs/{id}/stream` path which carries the string progress verbatim.

**Fix in v2:** a parallel structured event bus (`_structured_event_listeners_var` context-var alongside `_progress_listeners_var`) that the LangGraph compiler emits structured events into. Tracked as follow-up to #1016.

### Gotcha 2 — v1 ignores `RunAgentInput.messages` beyond the last user turn

The client sends the whole thread history in `messages`. For long threads this is wasteful; v1 **uses only the trailing user turn** as the topology input and logs the rest. v2 will reconcile against the audit log so clients can trust the server has the full thread.

**For consumers:** don't rely on the server having conversational memory of prior turns in v1. If you need memory, use the governed-memory skill backing in your topology (`persistence` category skills) — the agent-side memory is independent of the thread-side history.

### Gotcha 3 — `RunAgentInput.tools` (frontend tools) are logged but not honoured in v1

CopilotKit lets the frontend expose actions the agent can call. v1 accepts the field and logs it, but **the agent only sees skills granted by the topology**, not frontend tools. v2 adds a `frontend_tool` skill backing.

**For consumers:** if your UI needs the agent to invoke frontend-side actions (navigate, highlight, open modal), today that happens by convention — the agent emits a `TextMessageContent` with a well-known shape and the frontend parses it. Not elegant; fixed in v2.

## 10. Testing locally

Two terminals:

```bash
# Terminal 1 — runtime
uv run swarmkit serve reference/workspaces/dot --port 8000 --insecure

# Terminal 2 — minimal AG-UI client
# (A `just` target will ship with the #1016 implementation PR; until then
# the self-contained demo script lives at examples/ag-ui/demo.py.)
uv run python examples/ag-ui/demo.py
```

The demo script is a self-contained Python client that POSTs a `RunAgentInput` and prints the event stream with human labels. Reads from no SDK; grounded in `httpx` + manual SSE parsing. If this works, every AG-UI client will.

## 11. Checklist — wiring a new consumer app

Ordered, copy-pastable. Each step references a file in the repo or an exact URL from a dependency's docs.

1. **Confirm your workflow is chat-shaped.** Re-read §2 of this guide.
2. **Pick a frontend SDK.** CopilotKit React is the reference choice; `@ag-ui/client` for plain JS; `ag-ui-protocol` for Python; `.NET AGUI.Client` for C#. See https://docs.ag-ui.com/sdk for the current list.
3. **Point it at `swarmkit serve`.** Configure the SDK's `HttpAgent` with `url: "http://<swarmkit>/api/ag-ui/run"` and the standard auth headers (bearer + X-Owner).
4. **Decide which topology each conversation drives.** One Dot = one primary topology; the Dot YAML carries `primary_topology: <id>`. See `reference/apps/dots/` (phase 3 of #1018) for the pattern.
5. **Wire the chat UI.** `<CopilotChat>` with the HttpAgent above. Set `context: {topology: <topology-id>}` on every `RunAgentInput`.
6. **Render generative-UI payloads.** For each skill-output `kind` your topology emits, implement a React component. Register with CopilotKit's generative-UI renderer; see §8.
7. **Wire the HITL approval surface.** On `interrupt` outcome, render the `detail.artifact` payload with Approve/Reject buttons; on click, POST to `/api/ag-ui/run/resume`. See §7.
8. **Handle `RunError.code` values.** Map each error code to a user-readable message; see §5.
9. **Test the runtime side locally first** with the demo script at `examples/ag-ui/demo.py` to confirm it works before debugging the frontend.
10. **Read the gotchas section** before shipping anything to production users.

---

## Appendix A — operations-flavoured example migration

> Generic enough to apply to any ops app (incident response, hazard reports, support tickets, change requests).

Scenario: an existing non-chat ops app has incidents as database rows and users act on them via a dashboard. You want to add a chat-shaped surface so users can delegate incident work to an AI coworker.

1. **Model each incident as a thread.** AG-UI's `threadId` becomes the incident id. Every message in the chat is a run against the same `threadId`.
2. **Persist incident state as a SwarmKit space-pattern surface.** The incident's current status, assignee, severity are entries in a shared memory namespace `incident:<id>`. See `design/notes/spaces-pattern.md` (ships with #1017) for the pattern.
3. **One Dot per operator role.** "Incident Triage Dot" (classifies + routes), "Resolution Coordinator Dot" (drives steps to closure), "Postmortem Dot" (writes the retro). Each is a Dot YAML pointing at its primary topology.
4. **Approvals for status transitions.** Closing an incident, escalating to a different team, assigning to oncall — each is an HITL gate in the topology. Rendered as approve/reject in the chat per §7.
5. **Generative-UI payloads.** Status cards (`kind: incident_status`), action lists (`kind: action_list`), postmortem drafts (`kind: postmortem_draft`). Register renderers per §8.
6. **Keep the dashboard.** The chat surface complements the dashboard — users browse via the dashboard, delegate deep work via chat. Nothing is retired day one.

See the full `reference/apps/dots/` for the shape to copy against (shipping in phase 3 of #1018).

---

*Last updated:* draft phase. This document is ratcheted up as implementation of #1016 proceeds. If a claim here looks stale, check the git log for `docs/notes/ag-ui-integration.md`.
