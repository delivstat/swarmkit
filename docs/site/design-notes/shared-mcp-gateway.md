# One MCP gateway server per process, one registration per execution

**Status:** proposed

## Goal

Make a harness's granted MCP tools work on every execution in a process, not just the first few.

## The problem

Each harness execution starts its own `uvicorn` server for its gateway. Measured on 1.161.0: a
process serves roughly three of them, and every later one comes up **bound, audited with its full
tool list, and serving nothing**.

```
gw 1: OK   gw 2: OK   gw 3: OK   gw 4: EMPTY(200)   gw 5: EMPTY(200)
```

Ruled out, each by direct measurement:

| suspected | finding |
| --- | --- |
| server churn / port exhaustion | six create+teardowns then a healthy seventh — churn alone is harmless |
| teardown not completing | ~0.15s, and `serve_task` finishes |
| leaked tasks | 1–2 alive throughout |
| port reuse | ports distinct; old ports refuse connections |
| `connect_sse` raising | instrumented; it does not |
| a teardown race | a **delay makes it worse** — with 2s between gateways only the first survives |

So it is not rate, not resource exhaustion, and not our teardown. It is per-`uvicorn`-instance
state inside the uvicorn/MCP-SDK interaction, and the first instance in a process is the one that
works. 1.161.0 detects the dead gateway and fails fast rather than letting a toolless agent report
success — correct, but it leaves multi-execution runs unable to use their tools at all, and a
schema-correction retry is by definition a second execution.

## The change

**One `uvicorn` server per process.** Started lazily on first need, shared by every execution.

**One registration per execution.** Each registers its own `SseServerTransport`, its own MCP
`Server` with its own tool list, its own bearer token, and its own `agent_id`. The HTTP server is
shared; nothing else is.

```
http://127.0.0.1:<port>/gw/<registration-id>/sse
http://127.0.0.1:<port>/gw/<registration-id>/messages/
```

A spike confirms this survives where the current design does not — **7/7 executions healthy**,
each seeing only its own tool.

## What must not weaken

The current design gets isolation for free by giving each execution a whole server. Sharing one
means isolation becomes something we assert rather than inherit, so it is stated explicitly:

- **The token authorises one registration, not the gateway.** A token minted for registration A
  must be rejected on B's path. Checked per registration, never globally.
- **The registration id is unguessable** (`secrets.token_urlsafe`), so a path cannot be walked from
  a neighbouring execution.
- **Deregistration is immediate and total.** After an execution ends, its path 404s — a lingering
  URL that still served tools would outlive the governance decision that granted them.
- **`agent_id` comes from the registration**, not from a server-wide constant, so every
  `governed_mcp_call` is still attributed to the agent that made it. This is the one that would
  silently corrupt the audit trail if it were missed.
- **Concurrent executions are independent.** Two harness nodes in one run hold two registrations at
  once; neither can see the other's tools.

## Lifetime

Reference-counted. The server starts when the first registration is made and stops when the last is
released, so a process that runs no harness node never opens a socket, and a long-lived `serve`
process does not accumulate them. Failure to start is a failure of that execution, as today.

The health probe added in 1.161.0 stays: it is cheap, and it now checks a shared server rather than
a fresh one — which turns it from a workaround into a genuine liveness check.

## Non-goals

- Not a cross-process or long-lived service. Still one process, still torn down with it.
- Not a change to what a harness may call. The tool surface per execution is exactly what
  `_granted_mcp_tools` already computes.
- Does not explain the underlying uvicorn fault. It stops depending on the behaviour that breaks;
  the root cause remains unexplained and is recorded as such.

## Test plan

- Seven sequential registrations on one process are all healthy (the reproduction that fails today).
- An execution sees only its own tools.
- A token from one registration is rejected on another's path.
- A released registration 404s.
- Two concurrent registrations do not see each other.
- `agent_id` in the audit record follows the registration, with two agents interleaved.
- No harness node ⇒ no server started.

## Demo

`packages/runtime/demos/shared_gateway.py` — runs the reproduction that currently degrades and
shows every execution served.
