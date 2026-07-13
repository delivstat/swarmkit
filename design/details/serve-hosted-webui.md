---
status: accepted
---

# Serve-hosted web UI — the portal ships with the runtime

The web portal (`packages/ui`) is a separate Next.js app. Today you can only run it from a source
checkout with `pnpm dev` pointed at a `swarmkit serve` via `NEXT_PUBLIC_SWARMKIT_API`. So a user who
`pip install swarmkit-runtime` gets the CLI + API but **no portal** — and even the dev flow needs a
second process, a hardcoded API URL, and a CORS flag. This closes that gap: the portal is a
**static SPA served by `swarmkit serve` itself**, shipped as an optional install extra.

```
pip install "swarmkit-runtime[ui]"
swarmkit serve ./workspace        # portal AND API on http://localhost:8000 — one process, one port
```

## Why this works cleanly

`packages/ui` is a **pure client-side SPA**: no Next API routes, no server actions, no data-fetching
server components. So it exports to a static bundle (`output: 'export'` → `out/` of HTML/JS/CSS) that
needs **no Node at runtime** — any static file server, including FastAPI, can host it.

Served from the same origin as the API, two problems vanish:
- **No API-URL config.** The SPA calls the API with **relative** paths (`/api/topologies`), which
  resolve against whatever origin served the page — back to the same serve, which already has the
  workspace loaded. The workspace is chosen by the `swarmkit serve <workspace>` argument; the portal
  inherits it. (Today's `NEXT_PUBLIC_SWARMKIT_API` hardcode → relative base is the linchpin change.)
- **No CORS.** Same origin, so the `--cors-origin` dance is only needed for the detached-portal case.

## Packaging: an optional `swarmkit-webui` data wheel

The built assets ship in a **separate, data-only** package `swarmkit-webui`, pulled in by the
`swarmkit-runtime[ui]` extra:

- Base `swarmkit-runtime` stays lean — headless servers, CI, Lambda, embedded uses don't carry ~MBs
  of JS.
- `pip install "swarmkit-runtime[ui]"` adds the portal. `swarmkit serve` mounts it **if importable**,
  else runs headless (unchanged) — the portal is strictly additive.
- The UI ships on its own cadence; a UI-only change need not re-release the runtime.

`swarmkit_webui` exposes `static_dir() -> Path` (the bundled `out/`). Serve does
`try: import swarmkit_webui` and mounts it; absent ⇒ no portal, a one-line log.

## Serve integration

- **Static mount.** FastAPI `StaticFiles` serves the bundle at `/`; the API keeps its existing paths
  (`/health`, `/api/…`, `/run/…`, `/jobs/…`, `/observability/…`, `/mcp`). The API is mounted first so
  its routes win; the static mount is the catch-all.
- **SPA routing.** The app is a single-page app; deep links (`/composer`, `/jobs`) must serve the
  app shell so the client router takes over. Any GET that is not an API path and not a real static
  file falls back to `index.html`. To keep this dead simple we drop the one dynamic route —
  `/jobs/[id]` becomes `/jobs?id=<id>` (a query param) — so the export is fully static and every page
  is a real file; the fallback then only covers unknown paths defensively.
- **Auth.** The static assets are public (they contain no secrets — the SPA authenticates at runtime
  via the existing AuthProvider seam and stored token). The API stays behind the same auth as today.

## The one UI change with teeth

- `lib/api.ts`: `BASE = process.env.NEXT_PUBLIC_SWARMKIT_API ?? ""` (relative, same-origin). A build
  can still bake an absolute base for the detached-portal case; default is relative.
- `next.config`: `output: 'export'`, `images.unoptimized: true` (export has no image optimiser).
- `useSearchParams` pages need a Suspense boundary under export — wrap where the build flags it.
- `/jobs/[id]` → `/jobs` reading `?id=` (removes the only dynamic route; updates the ~2 link sites).

## Release pipeline

CI gains a step: build the UI static export, copy `out/` into `swarmkit_webui`'s package data, build
+ publish the `swarmkit-webui` wheel alongside the runtime. The built assets are **generated at
release, not committed** (like other codegen). A `just build-webui` target does the local build.

## Non-goals

- Not a Node server at runtime (static only).
- Not SSR / server components / API routes in the portal (it stays a thin client over the serve API).
- Not the detached-portal (CDN-hosted UI → remote serve) topology — supported via an absolute
  API-base build override, but not the default and not wired here.
- Invariant #8 (CLI is the v1.0 on-ramp) stands — this is **distribution** of the already-shipped UI
  (M12/M13), not a new UI surface.

## PR slices

1. **UI export-readiness** — relative API base; `output: 'export'` + `images.unoptimized`; Suspense
   fixes; `/jobs/[id]` → `/jobs?id=`. Verify `pnpm build` emits a working `out/`. (this repo)
2. **`swarmkit-webui` + serve mount** — the data package (`static_dir()`), `swarmkit serve` mounts it
   (StaticFiles + SPA fallback, graceful-absent), the `[ui]` extra, `just build-webui`, CI + docs.

## Test plan

- **UI (unit/build):** the static export builds; relative base resolves (`api.ts` unit — `BASE` is
  `""`, so a request path is origin-relative).
- **Serve (unit):** with a fixture static dir, `GET /` → index.html; a deep link (`/composer`) →
  index.html (SPA fallback); a real asset (`/_next/...`) → that file; an API path (`/health`) still
  hits the API, not the static mount; **no** webui package importable ⇒ serve runs headless (the
  portal routes 404, the API is unaffected).

## Demo plan

`pip install "swarmkit-runtime[ui]"` (or `just build-webui` locally) → `swarmkit serve ./examples/…`
→ open `http://localhost:8000` → the portal loads, same origin, on the workspace serve was given —
no second process, no env var, no CORS flag.

## Acceptance

- `swarmkit serve <ws>` with the `[ui]` extra installed serves the portal at its own origin; the
  portal talks to that serve's workspace with **no env var and no CORS config**.
- Without the extra, serve is byte-for-byte unchanged (headless).
- The base `swarmkit-runtime` wheel does not carry the JS bundle.
