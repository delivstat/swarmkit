---
title: Per-feature design notes
description: Directory for per-feature design notes. One file per feature, written before implementation.
tags: [meta, discipline]
status: active
---

# design/details/

Per-feature design notes. One file per feature, named `<scope>-<slug>.md`.

Every feature gets a design note **before** implementation starts (see root `CLAUDE.md` → "Feature delivery workflow"). The note is short — typically one page — and states:

1. **Goal** — what problem this solves in one sentence.
2. **Non-goals** — what this explicitly does not do.
3. **Design reference** — section(s) of `design/SwarmKit-Design-v0.6.md` this implements.
4. **API shape** — public surface (function signatures, CLI flags, schema fragments).
5. **Test plan** — what the tests cover; unit vs integration; test data.
6. **Demo plan** — how a human sees this working end-to-end. Terminal transcript, screenshot, `examples/` script, etc.
7. **Open questions** — anything deferred or unresolved.

The note lives on a branch and is merged via a design PR before the implementation PR opens. For trivial features the design note and implementation can share a PR if reviewable in one sitting.

Use `_template.md` as a starting point.
