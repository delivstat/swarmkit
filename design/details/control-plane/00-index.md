---
title: Control plane + auth — design (inventory → architecture → phase designs)
description: The central control plane + auth program. A comprehensive inventory of what SwarmKit already is as a runtime platform (docs 00–10), then the full design — architecture (11), auth (12), connector/registry (13), aggregation (14), artifact registry (15), fleet UI (16), growth loop (17), hardening/rollout (18). Design complete through Phase 8; implementation pending.
status: design complete (phases 0–8); implementation pending
---

# Control plane + auth — design

The central control plane + auth program (originating proposal: `design/details/fleet-control-plane.md`).
**Docs 00–10** are the Phase 0 inventory — *what does SwarmKit already provide as a runtime platform?*
— so the design is built against reality, not memory. **Docs 11–18** are the phase designs
(architecture → auth → connector → aggregation → registry → UI → growth loop → hardening).
**The design is complete through Phase 8; implementation has not started.**

Each inventory doc uses the same structure: *purpose · public interface (HTTP routes / CLI /
config / env) · data produced & consumed · extension seams · key file refs · control-plane
implications*. Doc 10 is the synthesis — the per-feature map of *single-instance today →
what the fleet panel surfaces/aggregates/manages*. Docs 11–18 each state goal · components/flows ·
data model · decisions · what-the-phase-builds · open questions.

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
| 11 | [Phase 1: Architecture](11-architecture.md) | goal/non-goals, the three planes, multi-instance + connection model, data model, trust boundaries, locked decisions |
| 12 | [Phase 2: Auth design + `server.auth` spec](12-auth.md) | threat model, three identity layers, two edges (token tiers / OIDC), the `server.auth` schema spec, default-secure, separation-of-powers |
| 13 | [Phase 3: Connector + instance registry](13-connector-registry.md) | `GET /capabilities`, heartbeat, enrollment, registry model, health, the pull-model reachability constraint |
| 14 | [Phase 4: Aggregation](14-aggregation.md) | OTel-collector for traces/metrics; central Postgres for audit/eval/usage; federated live-query; SwarmKit-specific signal views |
| 15 | [Phase 5: Artifact registry + versioning](15-artifact-registry.md) | content-hash + provenance versioning for all artifact types; governed push/pull sync; drift; schema-compat gate |
| 16 | [Phase 6: Fleet UI](16-fleet-ui.md) | the separate standalone app; pages; reuse `lib/api.ts`; OIDC + instance selector; conversational authoring; embed-vs-build |
| 17 | [Phase 7: Growth / self-improvement loop](17-growth-loop.md) | gap → propose → test → approve → publish → deploy; human-gated; proposal-only (no auto-activate) |
| 18 | [Phase 8: Hardening + rollout](18-hardening-rollout.md) | security review, migrating Minder/Sterling/vedanta, the default-secure breaking change, runbooks, GA criteria |
| 19 | [Fleet enrollment protocol + API-key credentials](19-fleet-enrollment-protocol.md) | standard client-agnostic register/join handshake; two-token flow (enrollment token → opaque API key → refresh); `InstanceState` full-state export; observed-state cache (offline-resilient); monitor vs manage scope; multi-fleet |
| 20 | [Fleet enrollment Phase 3 — manage + adopt](20-manage-and-adopt.md) | governed deploy over the membership credential (scope-aware serve auth-seam fallback: monitor→read, manage→deploy); adopt observed artifact into registry; multi-fleet visibility + eject |
| 21 | [Fleet identity — pinned public keys](21-fleet-identity.md) | self-certifying `fleet_id` from an Ed25519 public key; proof-of-possession at register; trust-on-first-use pinning + mismatch handling; foundation for signed pushes |
| 22 | [Signed pushes](22-signed-pushes.md) | the fleet signs each deploy over `deploy:kind:id:content_hash`; serve verifies against the pinned key so a stolen membership key alone can't push; opt-in enforcement; fixes the deploy wire mismatch |

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
