# ag-ui — AG-UI protocol demo

Minimal client against `POST /api/ag-ui/run` on a running `swarmkit serve`. Prints each
AG-UI event as it arrives over SSE. See `design/details/ag-ui-protocol.md` and
`docs/notes/ag-ui-integration.md` for the full protocol surface.

## Run

In one terminal:

```bash
cd examples/hello-swarm
SWARMKIT_PROVIDER=mock uv run swarmkit serve workspace
```

In another:

```bash
just demo-ag-ui   # or: uv run python examples/ag-ui/demo.py
```

You should see a `RunStarted` event, one or more `TextMessageContent` deltas, and a
`RunFinished` event with `outcome.type == "success"`.

## What this proves

- SwarmKit speaks AG-UI v1 (Lifecycle + Messages subset) out of the box.
- A CopilotKit-style frontend can drive any topology by posting `RunAgentInput` with
  `context.topology`.
- Richer events (tool calls, subagents, interrupts) are v2 — see the handoff guide.
