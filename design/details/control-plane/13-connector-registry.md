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

`instance_id` · `name` · `endpoint` · token refs (by tier) · `schema_version` · `capabilities`
(snapshot) · `health` (healthy / stale / unreachable) · `last_heartbeat` · `enrolled_at` ·
`labels` (env/region/owner). Extends [11](11-architecture.md) §5.

## Health model

Heartbeat interval + miss threshold → `healthy` → `stale` (missed) → `unreachable` (probe `/health`
fails). The panel surfaces state and suppresses control actions to unreachable instances.

## Reachability constraint (important, pull model)

The hybrid model ([11](11-architecture.md) D2) has the panel **pull** control over serve REST —
which requires the panel to **reach** the instance. Edge/home instances behind NAT/firewall
(e.g. Minder on a home router) are **not reachable** by an outbound pull.

- **v1 assumption:** instances are reachable — public endpoint, VPN, or **Tailscale**/equivalent.
  (Tailscale is the cleanest for edge boxes and aligns with the earlier serve-access guidance.)
- **Deferred option:** a reverse channel — the instance opens a persistent connection / polls the
  panel for commands — for instances that can't accept inbound. Flagged, not in v1.

This is a real boundary on which instances the v1 panel can *control* (vs merely *observe* via
push). Observability (push/heartbeat) works through NAT; control (pull) does not.

## Security

Per-instance scoped tokens; enrollment token single-use; revoke = remove the key + mark the
instance disabled; rotation per [12](12-auth.md) §6. The panel↔instance edge is the trust boundary —
compromise of a `run`/`admin` token is scoped to one instance.

## What Phase 3 builds

`GET /capabilities` on serve; the instance heartbeat client + the panel `POST /heartbeat`; the panel
instance registry (CRUD + health) + enrollment flow + token minting; capability-aware routing
(pick instances that can serve a requested provider/model); schema-skew warnings. Auth ([12](12-auth.md))
is a hard prerequisite.

## Open questions

- Panel-initiated vs instance-initiated enrollment (start panel-initiated).
- mTLS as a stronger panel↔instance option (vs bearer).
- Reverse channel for unreachable instances (deferred; revisit if edge fleets need control, not just
  observability).
