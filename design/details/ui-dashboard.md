---
title: SwarmKit UI — runtime dashboard, topology composer, skill authoring
description: Next.js app over swarmkit serve HTTP API. Three surfaces: dashboard (M12), composer + authoring (future).
tags: [ui, dashboard, serve]
status: implementing
---

# SwarmKit UI

## Goal

A web interface over `swarmkit serve` that provides runtime monitoring, topology browsing, canary management, and eventually visual topology editing and conversational skill authoring.

## Architecture

- Next.js 15 App Router with React 19
- Client-side only data fetching against `swarmkit serve` HTTP API
- Tailwind CSS v4 + shadcn/ui for components
- Zustand for client state
- No BFF — the FastAPI server already has CORS configured

The UI is a presentation layer. All business logic lives in `WorkspaceRuntime` (the same service backing CLI and HTTP). The UI calls the same endpoints as `curl`.

## Three surfaces

### 1. Runtime Dashboard (PR 1-5)

Monitor running workspace: health, jobs, canary, topologies, skills, triggers.

Pages:
- `/dashboard` — health card, recent jobs, canary summary, validation status
- `/jobs` — job list (polling), click-through to detail + SSE stream
- `/jobs/[id]` — job detail with live event stream
- `/topologies` — topology list with run button
- `/skills` — skill catalog with category badges
- `/archetypes` — archetype catalog
- `/canary` — canary route status, metrics, promote/rollback controls
- `/triggers` — trigger overview (cron/webhook)

### 2. Topology Composer (future)

Visual editor for topology YAML. Three views per design §15.2:
- Structure (org-chart tree)
- Relationships (per-agent detail)
- Network (node-and-edge graph)

Dependency: server needs write endpoints (`PUT /topologies/{id}`).

### 3. Skill Authoring Interface (future)

Chat-driven interface to the authoring swarm via `/conversations` API.
Uses the same authoring swarm the CLI uses — no separate flow.

## API client

Typed fetch wrappers in `lib/api.ts`. Base URL from `NEXT_PUBLIC_SWARMKIT_API` env var (defaults to `http://localhost:8000`).

## Non-goals

- No server-side rendering for API data
- No authentication UI (API key entry in settings dialog is sufficient for single-user)
- No mobile-first design
