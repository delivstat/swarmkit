# @swarmkit/control-plane-ui

The SwarmKit **fleet control panel** — a Next.js dashboard for connecting to and managing
multiple `swarmkit serve` instances. This is the UI half of the control plane; the API half
is the Python `swarmkit-control-plane` package.

It is a separate application from `packages/ui` (the single-instance dashboard), per design
decision D4 in `design/details/control-plane/16-fleet-ui.md`.

## Stack

- Next.js 15 (App Router) + React 19
- Tailwind CSS v4 + [shadcn/ui](https://ui.shadcn.com) component conventions (zinc theme,
  class-based dark mode)
- Biome for lint/format, `tsc` for typecheck — same toolchain as `packages/ui`

## Layout

Dashboard + sidebar shell. The sidebar nav follows the page set from design doc 16; live
routes are wired to the API, planned routes are shown muted until their slice lands.

| Route | Status |
| --- | --- |
| `/dashboard` (Fleet) | live — fleet overview + stat cards |
| `/instances` | live — registry table |
| Runs, Evals, Artifacts, Approvals, Authoring, Settings | planned |

## Develop

```bash
pnpm --filter @swarmkit/control-plane-ui dev   # http://localhost:3000
```

Point it at a running control plane. `NEXT_PUBLIC_CONTROL_PLANE_API` configures the panel base
URL (no host is hardcoded — it defaults to same-origin when unset). The panel's CORS is
config-only, so pass this UI's origin to the panel via `--cors-origin`:

```bash
# terminal 1 — the panel API (allow this UI's origin)
swarmkit-control-plane --cors-origin http://localhost:3000

# terminal 2 — this UI, pointed at the panel
NEXT_PUBLIC_CONTROL_PLANE_API=http://localhost:8800 pnpm --filter @swarmkit/control-plane-ui dev
```

See `.env.example` for configuration.
