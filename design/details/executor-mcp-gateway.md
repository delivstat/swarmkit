---
status: accepted
---

# Gatewayed MCP for harness executors

A harness node (Claude Code / opencode) today gets its own default tools, not the workspace's MCP
servers — `TaskSpec.mcp_tools` is captured but never forwarded, and no adapter wires `--mcp-config`.
The fix must not just "hand the harness the MCP servers": if the harness talks to an MCP server
**directly**, its tool calls bypass `GovernanceProvider.evaluate_action` — no permission tier, no
audit — which breaks invariant #4 (all skill/tool execution routes through the policy engine). So the
harness's MCP calls are **gatewayed**: routed back through SwarmKit's governance, then out to the
real server.

## The one governed path (reuse, don't fork)

Governance for an MCP call lives in one place — `_skill_executor._execute_mcp_tool`: resolve the
permission tier (`mcp_manager.get_permission(server, tool)`), and for anything but `open` call
`governance.evaluate_action(action="mcp:call:<server>:<tool>", …)`; on allow, `mcp_manager.call_tool`
+ audit. The gateway must reuse *exactly this*, not a second path. So step one is to **extract a
shared `governed_mcp_call(mcp_manager, governance, agent_id, server, tool, arguments, scopes)`** that
both the skill executor and the gateway call. One chokepoint, no bypass.

## Shape: an ephemeral, per-run, in-process MCP gateway

The existing `/mcp` (serve) and `swarmkit mcp-serve` both expose **topologies** as tools, not the
workspace's MCP servers — so neither is the surface we need, and we do not want to require
`swarmkit serve` to be running just to run a harness. Instead, for the lifetime of a harness node,
SwarmKit stands up an **ephemeral in-process MCP server** (the same `mcp` SDK machinery `mcp/_serve.py`
already uses) that:

1. **advertises** only the tools the agent is granted — its `mcp_tool` skills → `(server, tool)`,
   with each real tool's `inputSchema` pulled via `mcp_manager.list_tools`;
2. on `call_tool`, runs it through `governed_mcp_call` (evaluate_action + tier + audit) and then the
   real `mcp_manager.call_tool` — so every harness MCP call is governed and audited exactly as a
   model agent's would be;
3. is bound to a loopback (or container-reachable) address on an ephemeral port, protected by a
   **per-run bearer token**, and torn down when the node finishes.

This works for the primary `swarmkit run` path (no serve, no operator token) and, with the network
tweak below, for the container sandbox.

```
harness (Claude Code)  ──MCP/SSE──►  ephemeral gateway  ──governed_mcp_call──►  GovernanceProvider
   --mcp-config <file>                (agent's grants,      (evaluate_action,        │ allow
                                        per-run token)        tier, audit)           ▼
                                                            MCPClientManager.call_tool ──► real MCP server
```

## Wiring the harness

- **Config generation.** Build the harness-native MCP config — for Claude Code, a JSON with one
  server entry pointing at the gateway: `{"mcpServers": {"swarmkit": {"type": "sse", "url":
  "http://127.0.0.1:<port>/sse", "headers": {"Authorization": "Bearer <token>"}}}}`. Write it into
  the sandbox (a temp file under the worktree, git-ignored) and expose its path as a new substitution
  var **`task.mcp_config`**.
- **Adapter consumption (data, not code).** The adapter declares how its harness takes the config,
  e.g. `claude-code.yaml`:
  ```yaml
  optional_args:
    - when: task.mcp_config
      args: [--mcp-config, "{task.mcp_config}"]
  ```
  `build_command` already appends a `when:` group only when its var is set — no engine change. A
  harness with no MCP-config flag simply omits the group and is unaffected.
- **Grant filtering.** The gateway advertises only the agent's granted MCP tools, so the harness sees
  the same tool surface a model agent would — and every call is still tier-checked at call time
  (advertise ≠ authorize).

## Container sandbox

The harness runs inside the container; the gateway runs on the host. Reachability reuses the #20 http
path: bind the gateway to the docker bridge (not just loopback), launch the container with
`--add-host=host.docker.internal:host-gateway` (Linux) so the config URL is
`http://host.docker.internal:<port>/sse`, and add that host to the egress **allowlist** (it flows
through the same `_effective_allow` merge that already handles http MCP servers). `network: deny`
means no gateway — the harness runs tool-less (documented). The per-run bearer token means even a
container that reached the port can't call tools it wasn't granted.

## Decisions

- **Gatewayed, not direct.** Direct is less code but ungoverned/unaudited — a non-starter against
  invariant #4. Gatewayed keeps the exact governance a model agent gets.
- **Ephemeral in-process, not the serve `/mcp`.** `/mcp` is topology-only and would force
  `swarmkit serve`; a per-run gateway is self-contained and works for `swarmkit run`. (A future
  option: let `swarmkit serve` host a persistent governed tool-proxy for long-lived harnesses —
  deferred.)
- **SSE/HTTP transport, not stdio.** A stdio gateway subprocess would run *inside* the container with
  no access to the host's MCP configs/governance — it can't work for the sandbox. HTTP crosses the
  boundary and reuses #20.
- **Per-run token + grant-filtered surface.** Least privilege: the gateway exists only during the
  run, exposes only granted tools, authorizes every call.

## Non-goals

Exposing MCP *resources*/*prompts* (tools only for v1); a persistent shared gateway; bridging stdio
MCP servers *into* a `deny`-network container; non-Claude MCP-config formats beyond what each bundled
adapter declares (opencode etc. get their own `optional_args` when verified).

## PR slices

1. **Extract `governed_mcp_call`** — one shared governed path; `_execute_mcp_tool` delegates to it.
   Pure refactor, existing tests stay green. (design = this note)
2. **Ephemeral gateway + config gen + `task.mcp_config`** — the in-process MCP server (grant-filtered,
   governed), per-run token, config generation, the `task.mcp_config` substitution var, and
   `claude-code.yaml` `optional_args`. Native (worktree) path. Unit tests (gateway lists only grants;
   a call is governed + audited; denied tier refuses) + a gated e2e (real `claude` + a trivial real
   MCP server: the harness calls a workspace tool through the gateway and the call is audited).
3. **Container-sandbox reachability** — bind host-reachable, `--add-host` host-gateway, allowlist the
   gateway host. Unit (arg assembly) + gated e2e (a sandboxed harness reaches the gateway; `deny`
   yields no gateway).

## Test plan

- **Unit (no harness):** `governed_mcp_call` allow/deny/tier + audit; the gateway advertises only the
  agent's granted `(server, tool)` pairs; a `call_tool` on the gateway routes through
  `evaluate_action` and refuses a denied tier; config-JSON generation shape; `task.mcp_config`
  appears in the substitution context + argv only when set.
- **Gated e2e (`SWARMKIT_E2E=1`):** stand up a trivial local MCP server, run a real harness with the
  generated `--mcp-config`, assert it lists + calls the workspace tool and that an audit event was
  recorded; the container variant reaches the gateway over `host.docker.internal`.

## Demo plan

`demos/mcp_gateway.py`: a workspace with one MCP tool, a harness archetype; run it, show the harness
calling the workspace tool **through** the gateway, and print the `mcp:call:*` audit event proving the
call was governed (not a direct, unaudited call).

## Acceptance

- A harness node can list + call the workspace's granted MCP tools; each call emits an
  `mcp:call:<server>:<tool>` audit event and is tier-checked — **no ungoverned direct call path**.
- The tool surface is the agent's grants only; a denied tier refuses at the gateway.
- No `task.mcp_config` (no adapter support / no MCP tools) ⇒ unchanged behavior; the gateway isn't
  started.
- Container: an `allowlist` harness reaches the gateway via `host.docker.internal` (auto-allowlisted);
  `deny` runs tool-less, documented.
- Eject: the generated MCP config + `--mcp-config` arg are derivable from the adapter + workspace, so
  an ejected node can reproduce them.
