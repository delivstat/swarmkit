---
status: accepted
---

# UI design system — shadcn/ui + Geist, neutral-dark

The workspace UI (`packages/ui`) grew as hand-rolled Tailwind: raw `<input>`/`<select>`/`<button>`
elements with inline `style={{ background: "var(--bg)" }}` and ad-hoc class strings. It works, but the
surfaces drift (inconsistent inputs, focus rings, spacing, radii) and every new surface re-invents the
same primitives. We want the product to look like the Vercel / rynko family — the recognizable
neutral-dark, Geist-typeset, tight-spacing aesthetic.

The stack is already shadcn's exact foundation, so this is an adoption, not a rewrite:

| shadcn needs | We already have |
| --- | --- |
| Tailwind | Tailwind **v4** (`@import "tailwindcss"`, CSS-first — no `tailwind.config.js`) |
| `cn()` (clsx + tailwind-merge) | `clsx` + `tailwind-merge` in deps |
| lucide icons | `lucide-react` |
| CSS-variable theming, light + dark | `--bg`/`--fg`/`--border`/… in `globals.css` |

What adoption adds: Radix primitives (`@radix-ui/react-*`), `class-variance-authority`, the `geist`
font package, a `components.json`, and the copied-in component source under `components/ui/`. shadcn is
**not an npm dependency** — the component source lives in our repo, which fits invariant #1 (the UI owns
its code, it is a thin layer, not a black box).

## Non-goals

- Not changing what the UI *does* — this is presentation only. Every surface round-trips through the
  same YAML/JSON + serve API (invariant #1); audit + review stay read-only (invariant #4).
- Not adding a component library as a dependency — shadcn source is copied in and owned.
- Not a full theming engine. One default look (neutral-dark) shipped now; a runtime light/dark toggle
  is a later, optional slice (tokens for both are defined so it's a small follow-up).

## Theme

- **Font.** Geist Sans + Geist Mono via the `geist` package (`geist/font/sans`, `geist/font/mono`) wired
  through `next/font` in `app/layout.tsx`. Mono for code/ids/audit.
- **Palette.** shadcn "new-york" style, **neutral** base color. Tailwind v4 means CSS-first tokens: a
  `:root` (light) and `.dark` block of the shadcn token set (`--background`, `--foreground`, `--card`,
  `--muted`, `--border`, `--primary`, `--ring`, …) in `oklch`, plus a `@theme inline` block mapping them
  to Tailwind color utilities (`--color-background: var(--background)` …) so `bg-background`,
  `text-muted-foreground`, `border-border` etc. work.
- **Default look = dark.** `<html class="dark">` locks the requested neutral-dark aesthetic
  deterministically. Light tokens are defined for the future toggle but not the default.
- **Legacy vars kept as aliases.** The old `--bg`/`--fg`/`--border`/`--accent` names are re-pointed at
  the new tokens (`--bg: var(--background)` …) so surfaces not yet migrated keep rendering during the
  incremental sweep. Removed once every surface is migrated.

## Primitives (`components/ui/`)

Copied-in, owned: `button`, `input`, `textarea`, `label`, `select`, `card`, `badge`, `tabs`, `dialog`,
`dropdown-menu`, `tooltip`, `checkbox`, `switch`, `separator`, `scroll-area`, `skeleton`. Each is the
standard shadcn component (Radix primitive + CVA variants + `cn()`), unmodified so future upstream
diffs are easy to reconcile.

## Migration plan (incremental — one arc of PRs)

1. **Foundation (this note's PR).** Deps + `components.json` + `lib/utils.ts` (`cn`) + theme in
   `globals.css` + all `components/ui/` primitives + migrate the **shell**: `app/layout.tsx` (Geist),
   `components/layout/sidebar.tsx`, `components/card.tsx`, `components/status-badge.tsx`.
2. **Designer surfaces.** `components/schema-form.tsx` (swap raw input/select/textarea/button for
   primitives, schema-driven logic untouched), composer, topology canvas nodes/panels.
3. **Monitoring surfaces.** dashboard, jobs, job, audit, gates, canary — Card/Table/Badge/Skeleton.
4. **Catalog + authoring surfaces.** skills, topologies, archetypes, triggers, chat console.

The legacy `--bg`/`--fg` aliases keep unmigrated surfaces intact between steps, so each PR is
independently shippable and green.

## Test plan

- **Primitives.** Vitest smoke tests: each primitive renders, `Button` applies its variant/size
  classes, `cn()` merges + de-dupes conflicting Tailwind classes.
- **No regression.** The existing 61 UI tests stay green through every step (schema-form logic is
  untouched; only its rendered elements change).
- **Build.** `next build` (static export, `output:'export'`) succeeds at every step — the serve-hosted
  portal (swarmkit-webui) must keep building.

## Demo / acceptance

The portal (`swarmkit serve`, `[ui]` extra) renders in neutral-dark with Geist throughout: a consistent
sidebar, cards, inputs, and badges; focus rings and radii uniform across surfaces; no raw-Tailwind
one-off inputs left. Side-by-side before/after screenshots in each PR body.
