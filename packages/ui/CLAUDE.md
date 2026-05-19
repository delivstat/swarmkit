# CLAUDE.md — packages/ui

## Package identity

Next.js web app with three surfaces (design §15):

1. **Topology Composer** — hybrid form + visual editing of topology files. Three views: Structure (org chart), Relationships (per-agent zoomed), Network (flat communication graph).
2. **Skill Authoring Interface** — conversational front-end to the Skill Authoring Swarm. Chat-driven with structured previews and test execution.
3. **Runtime Dashboard** — review queues, run history, audit log viewer, skill gap log, archetype + skill catalog.

## Status

**Deferred to v1.1.** The v0.6 design confirms the UI ships as v1.1 — the v1.0 on-ramp is conversational CLI via the three authoring swarms. This package is scaffolded for when v1.1 work starts.

Do not add UI features that compete with or bypass the authoring swarms. The UI is a *presentation layer on top of* the same authoring swarms the CLI uses. One mechanism, two front-ends.

## Non-negotiable invariants

1. **The UI is a thin layer over topology files and the runtime HTTP API.** It must not maintain its own state format — round-trips through YAML/JSON.
2. **Validation goes through `@swarmkit/schema`.** Do not inline schema knowledge.
3. **Skill authoring in the UI is the same Skill Authoring Swarm** invoked via the runtime HTTP API. The UI does not have a separate "lite" authoring flow.
4. **Audit log and review queue are read-only in the UI.** No UI affordance bypasses the append-only media pillar (design §8.3).

## Stack

- Next.js 15 (app router)
- React 19
- TypeScript strict
- Zustand for client state (lightweight, no context nesting)
- Biome for lint + format
- Vitest for unit tests
- Playwright (later) for e2e once surfaces exist

## Commands

```bash
pnpm --filter @swarmkit/ui dev
pnpm --filter @swarmkit/ui build
pnpm --filter @swarmkit/ui typecheck
```
