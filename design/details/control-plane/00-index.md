---
title: Control plane — runtime platform inventory (source of truth)
description: Phase 0 of the central control plane + auth program. A comprehensive inventory of what SwarmKit already is as a runtime platform — every subsystem, its public interface, the data it produces/consumes, its extension seams, and what a multi-instance control plane would surface, aggregate, manage, or version. This is the catalog the control plane builds against; later phases (architecture, auth, connector, registry, UI) key off it.
status: phase 0 — inventory (draft, source of truth)
---

# Control plane — runtime platform inventory

This is **Phase 0** of the control-plane + auth program (see `design/details/fleet-control-plane.md`
for the originating proposal and the program task list in the PR description). It answers one
question exhaustively: **what does SwarmKit already provide as a runtime platform?** — so the
control plane is designed against reality, not memory.

Each subsystem doc uses the same structure: *purpose · public interface (HTTP routes / CLI /
config / env) · data produced & consumed · extension seams · key file refs · control-plane
implications*. Doc 10 is the synthesis — the per-feature map of *single-instance today →
what the fleet panel surfaces/aggregates/manages*.

## Table of contents

| # | Doc | Covers |
|---|---|---|
| 01 | [Runtime core](01-runtime-core.md) | resolver, LangGraph compiler, tool loop, planning/synthesis, run lifecycle |
| 02 | [Serve HTTP API + automation](02-serve-api.md) | every route, **the auth seam**, triggers/schedules, canary — the connector contract |
| 03 | [Provider seams](03-provider-seams.md) | Model / Governance / Audit / Notification / ContextCompressor / Auth providers |
| 04 | [Persistence & state](04-persistence-state.md) | sqlite store, traces, audit, run-state, checkpoints, memory, on-disk artifacts |
| 05 | [Identity, governance & IAM](05-identity-governance-iam.md) | scopes, reserved-for-human scopes, separation of powers, decision skills, circuit breakers, HITL |
| 06 | [Observability & eval](06-observability-eval.md) | OTel spans/metrics, run trace, eval harness (M15), intent drift (M7), prompt ring buffer |
| 07 | [Schema](07-schema.md) | the five artifact types, the workspace.yaml surface, versioning, validators, codegen |
| 08 | [CLI surface](08-cli.md) | every `swarmkit` command + status (implemented vs stub) |
| 09 | [UI](09-ui.md) | pages, the api.ts→serve coupling, composer reality, gaps vs design |
| 10 | [Capability map (synthesis)](10-capability-map.md) | current → control-plane responsibility, per feature |

## Cross-cutting findings (read these first)

These surfaced repeatedly across subsystems and shape the architecture/auth phases:

1. **An auth seam already exists — but defaults open and is unschematized.** `auth/`
   ships `AuthProvider` (ABC) + `NoneAuthProvider`, `APIKeyAuthProvider` (bearer), `JWTAuthProvider`
   (JWKS). `serve` runs `auth_middleware` and selects a provider via `_build_auth_provider`
   reading a **`server.auth`** block — **which is NOT in the workspace JSON Schema** (undocumented,
   unvalidated). Default is `NoneAuthProvider` (wildcard scope) and the CLI default bind is
   `--host 0.0.0.0:8000`. So the auth phase is **harden + schematize + tier the existing seam**,
   not greenfield. Details in [02](02-serve-api.md) §Auth and [05](05-identity-governance-iam.md).

2. **Almost all persisted state is per-instance SQLite + local JSON.** Jobs, conversations,
   usage, audit, traces, checkpoints, run-state, memory all live under `.swarmkit/` on one box.
   Postgres is configurable for some backends but largely unimplemented (falls back to sqlite).
   Multi-instance aggregation is the central data-plane problem. Details in [04](04-persistence-state.md).

3. **Observability is OTel-native already.** A mature span/metric surface (`swarmkit.*`) exports
   via OTLP. The control plane should **aggregate via the OTel ecosystem**, not rebuild trace UI;
   its unique value is the SwarmKit-specific signals (eval results, intent drift, governance
   decisions, skill gaps, compression ROI). Details in [06](06-observability-eval.md).

4. **Separation of powers constrains what the panel may do.** Reserved-for-human scopes
   (`skills:activate`, `mcp_servers:deploy`, `topologies:modify`, `iam:modify`; `audit:modify`
   exists for no one) must stay un-grantable to the panel. Transport authn (who may call the API)
   and governance identity (whose approval counts) are **distinct layers**. Details in [05](05-identity-governance-iam.md).

5. **Topologies version (canary); skills/archetypes/workspace do not.** Canary routes give
   `(topology, version)` weighted routing + promotion. There is no central versioned registry for
   skills/archetypes, and no cross-instance artifact store. Details in [02](02-serve-api.md) §Canary, [07](07-schema.md).

6. **The UI is single-instance + zero-auth, but the API client is cleanly decoupled.**
   `lib/api.ts` is REST-over-`NEXT_PUBLIC_SWARMKIT_API`; reusable for a panel once auth headers,
   instance selection, and 401 handling are added. Composer is YAML-editing only. Details in [09](09-ui.md).

## How to use this inventory

- **Designing the control plane?** Start with [10](10-capability-map.md), then drill into the
  subsystem doc for each capability.
- **Designing auth?** [02 §Auth](02-serve-api.md) + [05](05-identity-governance-iam.md) are the
  source of truth for the existing seam and the constraints it must respect.
- **Building the connector?** [02](02-serve-api.md) is the contract; [04](04-persistence-state.md)
  is what flows over it.

> Provenance: assembled from a parallel read-only sweep of the codebase. File:line references are
> accurate as of this branch; verify before depending on a specific line. "Control-plane
> implications" sections are analysis, not current behavior — they are clearly separated from the
> "current state" facts in each doc.
