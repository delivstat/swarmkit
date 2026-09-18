---
title: A2A trace deep-link — click a remote call, open the callee's job
description: Federation slice 3. A SwarmKit callee returns a browsable job URL; the caller carries it on the trace step so the portal waterfall links to the other instance's job, and the fleet UI opens it in-panel through the connector.
tags: [a2a, interop, observability, ui, fleet]
status: proposed
---

# A2A trace deep-link

Slice 3 of A2A federation (`a2a-federation.md`). Slice 1 returns the callee's `run_id`; this makes
it a click. The data link already exists (the A2A `contextId` is the caller's correlation id, and
`a2a.remote_usage` carries `remote_run_id` + `endpoint`); this adds the navigational one.

## Goal

From the caller's run — its `swarmkit trace` waterfall, its run detail in the portal, or the fleet
UI — a person can open the **callee's** job detail on the other SwarmKit instance in one click.

## Non-goals

- **Not for non-SwarmKit remotes.** Gated by the federation extension; no `job_url`, no link.
- **Not cross-instance SSO.** A raw serve→serve link lands on the other portal's own login. The
  fleet path is the authorized one (through the connector).
- **Not embedding the remote UI.** A link (new tab) for serve; an in-panel fetch for fleet.

## Design

### The callee returns a browsable URL

The callee is authoritative about where its own portal is (the A2A `endpoint` may be a proxy
address). `task_from_job` adds to `metadata.swarmkit.observability`:

```json
{ "events": true, "audit": true, "job_url": "<portal root>/job?id=<run_id>" }
```

built from the serve instance's portal root and its job route. `portal_url` (the root) is included
too, so a caller can construct other links without hard-coding the route.

### The caller carries it on the trace step

`_call_remote` already records `a2a.remote_usage`. It also stamps the **trace step** for the
agent-skill call with `remote_run_id`, `endpoint` and `job_url`, so `swarmkit trace` prints
`→ remote run <id> on <name>  [<job_url>]` and the portal has the link inline on the waterfall
node, not only in the audit log.

### Serve portal renders the link

The run/waterfall view renders the remote agent-skill node with an "↗ open on `<name>`" affordance
to `job_url` (new tab). The audit row for `a2a.remote_usage` links the same way.

### Fleet UI opens it in-panel

The fleet enrolls the instances and already pulls their jobs/audit (instance-scoped observability;
`25-ui-trace-deeplink.md`). Given `remote_run_id` + `endpoint`, the fleet resolves the endpoint to
the **enrolled instance** and opens that instance's job detail **inside the panel through the
connector** — authorized via the fleet, no separate login. This is the federated-waterfall
experience note 25 threads a run id through the UI for; the A2A hop is one more edge in it.

## Test plan

- `task_from_job` includes `observability.job_url` pointing at the callee's portal job route;
  behind an `identity.url` proxy the `job_url` uses the configured root, not the request host.
- Round-trip: the caller's trace step for the agent-skill call carries `remote_run_id` + `job_url`;
  `swarmkit trace` prints the remote link.
- Portal: the waterfall node renders the link (UI test).
- Fleet: `remote_run_id` + `endpoint` resolve to an enrolled instance and open its job via the
  connector (control-plane-ui test).

## Demo plan

Two serve instances plus the fleet: call B from A, open A's run in the portal, click the remote
node → B's job page; then the same from the fleet waterfall, opened in-panel.
