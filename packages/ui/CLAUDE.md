# CLAUDE.md — packages/ui

## Package identity

Next.js web app with three surfaces (design §15):

1. **Topology Composer** — hybrid form + visual editing of topology files. Three views: Structure (org chart), Relationships (per-agent zoomed), Network (flat communication graph).
2. **Skill Authoring Interface** — conversational front-end to the Skill Authoring Swarm. Chat-driven with structured previews and test execution.
3. **Runtime Dashboard** — review queues, run history, audit log viewer, skill gap log, archetype + skill catalog.

## Status

**Active — building the workspace UI** per `design/details/workspace-ui.md`. Posture (decided): a **co-equal peer** to the CLI, **not** its replacement — invariant #8 stands, the conversational CLI remains the v1.0 on-ramp. This app is the per-**workspace** surface (see/monitor/run/author one workspace); `packages/control-plane-ui` is the separate per-**fleet** surface. Built slice by slice (auth → monitor → schema-driven designer → topology graph → conversational console).

Do not add UI features that compete with or bypass the authoring swarms. The UI is a *presentation layer on top of* the same authoring swarms + serve API the CLI uses. One mechanism, two front-ends. The conversational front door stays the default authoring path; the designer is the inspect/refine/reference layer.

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
