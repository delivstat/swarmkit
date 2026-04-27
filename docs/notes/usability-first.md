---
title: Usability-first
description: Per-PR checklist for friction reduction. Swael trades on being easy to start and easy to grow; every PR reinforces that or dilutes it.
tags: [discipline, usability, review]
status: active
---

# Usability-first

Swael's design doc §7 already declares "Ergonomics determine adoption." This note is the operational version — a checklist every PR reviewer (human or tool) runs through before approving.

## The rule

**Every feature has a usability story.** If a feature would cause a user to reach for the docs, the CLI, a forum post, or an LLM to figure out what to do, the feature is not done. Remove the friction with the minimum intervention that works — good errors beat new subcommands, good defaults beat required flags, a conversational path beats YAML editing where the target user is non-technical.

"Minimum intervention" is the important clause. Over-tooling is a failure mode too. A `swael doctor` that duplicates `just schema-codegen-check` adds friction by adding surface.

## Per-PR checklist

For any non-trivial PR touching user-facing surface (CLI, config, errors, authoring flows):

- [ ] **What friction does this introduce?** One sentence. If "none," consider whether you're right — every new subcommand, flag, or config key is latent friction.
- [ ] **What removes it?** A specific tool, error message, default, or conversational flow.
- [ ] **Who's the target user?** Developer / analyst / both. An analyst-facing feature needs a non-terminal path (authoring swarm, UI). A developer-facing one can lean on the CLI + docs.
- [ ] **First-time-user test:** could a user with no prior Swael context complete this task without reading the design doc? If no, what changed their position?

Purely-internal refactors and library-level work are exempt. The checklist targets anything that touches the user's hands.

## Anti-patterns

- **Docs as tooling.** "The user will read the docs" is a deferral, not a solution. Works for developers and architects; fails silently for analysts and first-time users. Don't ship a feature that requires reading the design doc to use.
- **Error messages that are correct but useless.** `ValidationError: '#/agents/root/role' value not in enum` is correct; `Agent 'root' has role 'supervisor' which is not allowed. Roots must use 'root'; leaders and workers are enforced by archetype` is useful. Errors are documentation consumed at the exact moment a user needs it.
- **New subcommand for every new feature.** Subcommands are surface area. Prefer extending an existing command, improving its output, or generating the artifact automatically over adding `swael new-thing`.
- **Required flags for config that has a sensible default.** `--input` on `swael run` should accept stdin, a file, or nothing (interactive). Not "required: true."
- **Failure modes that blame the user.** "Invalid configuration" or "unexpected error" throw the problem back. Say what happened, why, and what to try.

## Where this applies

Every milestone's exit demo grows one criterion: "a first-time user can complete this without reading the design doc." That's the test, applied at the natural point.

The concierge agent / knowledge MCP server story (tasks #24, #25) is the broader investment this checklist supports — see [llm-friendly-knowledge.md](./llm-friendly-knowledge.md). This note covers the per-PR discipline; that note covers the platform-level delivery mechanism.

## See also

- `design/Swael-Design-v0.6.md` §7 Architectural Principles (Ergonomics determine adoption)
- `design/Swael-Design-v0.6.md` §3.4 The first-run promise
- `docs/notes/llm-friendly-knowledge.md` — how LLMs help close the gap
- Task #23: human-readable `swael validate` errors — the first concrete landing of this discipline
