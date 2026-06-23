# 13 — Phase 3: Connector + instance registry

Builds on the architecture ([11](11-architecture.md)) and auth ([12](12-auth.md)). Designs how
instances are enrolled, advertised, and kept healthy — the data-plane foundation. Implementation
follows; this is the spec.

## Goal

Turn `serve` into a registrable **connector** and give the panel an **instance registry**: enroll
an instance, learn its capabilities, track its health, and route control to it — all over the
authenticated REST surface ([02](02-serve-api.md), [12](12-auth.md)).

## Connector additions to `serve`

- **`GET /capabilities`** (new; `serve:read`) — advertises what an instance can do: `serve` version,
  `swarmkit-schema` version, available model providers + models, topologies (+ versions),
  governance provider, enabled features (compression backend, eval, canary), and resource hints.
  The panel snapshots this at enroll and refreshes on heartbeat/﻿drift.
- **Heartbeat (instance → panel):** a small outbound client on the instance posts to the panel's
  `POST /instances/{id}/heartbeat` every N seconds with `{status, schema_version, cheap counters}`.
  Lets the panel track liveness without polling every instance. Authenticated by the instance's
  panel-issued credential.
- All connector endpoints are gated by the auth seam ([12](12-auth.md)); `/health` stays exempt.

## Enrollment flow

1. Operator adds an instance in the panel (name + endpoint).
2. Panel mints **per-instance, per-tier tokens** ([12](12-auth.md) §4) and shows the operator the
   `server.auth` snippet (token as a `key_ref`, never a literal).
3. Operator configures it on the instance and reloads/restarts serve.
4. Panel verifies: `GET /health` (liveness) + `GET /capabilities` (with the `run`/`read` token) →
   records the capability snapshot → marks the instance **enrolled**.
5. Instance begins heartbeating.

A one-time enrollment code (instance-initiated) is an alternative for instances that can't be
reached for the initial pull (below) — deferred unless needed.

## Registry data model (`Instance`)

`instance_id` · `name` · `endpoint` (Mode A) · `connection` (`direct` | `poll`) · token refs
(by tier) · `schema_version` · `capabilities` (snapshot) · `health` (healthy / stale / unreachable) ·
`last_heartbeat` (or `last_poll` in Mode B) · `enrolled_at` · `labels` (env/region/owner). Extends
[11](11-architecture.md) §5.

## Health model

Heartbeat interval + miss threshold → `healthy` → `stale` (missed) → `unreachable` (probe `/health`
fails). The panel surfaces state and suppresses control actions to unreachable instances.

## Connection modes (control edge)

The control edge ([11](11-architecture.md) D2) is logically **pull** — the panel is the source of
truth for what should happen on an instance. But the *transport direction* has two modes, chosen
per instance at enrollment (`Instance.connection`). Observability is always push (OTLP + heartbeat)
in both, and works through NAT regardless.

### Mode A — direct (panel → serve REST)

The panel calls the instance's serve API directly ([02](02-serve-api.md)). Simplest (reuses serve
as-is, lowest latency), but **requires the panel to reach the instance** — public endpoint, VPN, or
Tailscale. Right for datacenter / reachable instances.

### Mode B — poll connector (instance → panel, outbound only)

For instances that can't accept inbound (NAT'd / home / edge — e.g. **Minder on a home router**), a
lightweight **poll connector** runs alongside serve and makes **outbound HTTPS only** — no inbound
port, no VPN. It inverts the transport while keeping the panel as the decider (the runner pattern,
à la CI self-hosted runners). *Named "connector," not "agent," to avoid clashing with swarm agents;*
*CLI sketch: `swarmkit connect <panel-url>`.*

Protocol (per-instance **command queue** on the panel):

1. `POST /instances/{id}/poll` (long-poll, ~30s) with `{status, schema_version, capabilities_hash}`
   → returns `{commands: [{cmd_id, verb, args}]}`. **Heartbeat + capability refresh fold into the
   poll** — no separate heartbeat needed in this mode.
2. The connector executes each command against **local serve over loopback** (trusted): `verb`
   maps to a serve call (`run`, `cancel`, `validate`, `api.*` CRUD, `reload`, `usage`, `capabilities`).
3. `POST /instances/{id}/commands/{cmd_id}/result` with `{status, output|error}`. Idempotent by
   `cmd_id`; at-least-once with dedup; run progress streamed as incremental results (or the panel
   enqueues a follow-up `job-status` command).

Auth is the same token model ([12](12-auth.md)) with the **direction inverted**: the connector
authenticates *to the panel* with the per-instance token; the panel authorizes which commands it
enqueues, **bounded by the instance's granted tier** (`read`/`run`/`admin`), and the connector
**re-validates** each command is within tier (defense in depth). Loopback serve calls use local
trust.

**Separation of powers holds** ([05](05-identity-governance-iam.md)): a `deploy` command is only
enqueued *after* its human-gated approval ([15](15-artifact-registry.md), [17](17-growth-loop.md));
the connector executes an already-approved action, and the run still passes through `evaluate_action`
on the instance. Both sides audit (panel: enqueue + result; instance: execution).

### Choosing a mode

| | Mode A (direct) | Mode B (poll connector) |
|---|---|---|
| Reachability | needs inbound (VPN/Tailscale/public) | **outbound-only — works through NAT** |
| Latency | lower (direct REST) | poll/long-poll bounded |
| Moving parts | none (reuse serve) | + connector process + command queue |
| Best for | datacenter / reachable | edge / home / NAT (Minder) |

Per-instance, operator-selected. This removes the earlier "control needs reachability" limitation:
**edge instances get full control via Mode B**, not just observe-only.

## Security

Per-instance scoped tokens; enrollment token single-use; revoke = remove the key + mark the
instance disabled; rotation per [12](12-auth.md) §6. The panel↔instance edge is the trust boundary —
compromise of a `run`/`admin` token is scoped to one instance.

## What Phase 3 builds

`GET /capabilities` on serve; the instance heartbeat client + the panel `POST /heartbeat`
(Mode A); **the poll connector (`swarmkit connect`) + the panel command-queue endpoints
(`/poll`, `/commands/{id}/result`) (Mode B)**; the panel instance registry (CRUD + health +
`connection` mode) + enrollment flow + token minting; capability-aware routing; schema-skew
warnings. Auth ([12](12-auth.md)) is a hard prerequisite for both modes.

## Open questions

- Panel-initiated vs instance-initiated enrollment (start panel-initiated).
- mTLS as a stronger panel↔instance option for Mode A (vs bearer).
- Poll connector: long-poll vs short-poll interval default; command-queue durability (in-memory vs
  Postgres-backed); how run-progress streaming maps onto the result channel.
- Whether the poll connector is a separate process or a `serve` sub-mode (lean: a thin separate
  process so it can run where serve can't bind, but shares the workspace + token).
