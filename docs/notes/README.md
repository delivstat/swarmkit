# docs/notes/

Cross-cutting practical notes — the "things that don't belong anywhere else but must not be forgotten" drawer.

## What belongs here

- **Discipline notes.** Dual-surface rules, regeneration workflows, multi-package change checklists. Anything of the form "when you do X, remember to also do Y."
- **Gotcha notes.** Non-obvious behaviour that caught us once and will catch us again. Why a workaround exists. What a confusing error actually means.
- **Decision shortcuts.** "We already decided X" pointers with a link to the authoritative design. Stops the same debate recurring.

## What does not belong here

- **Architecture decisions** — those live in `design/` (source of truth) and `design/details/` (per-feature designs). This directory does not contain decisions; it contains *reminders to apply* them.
- **User-facing documentation** — `docs/concepts/`, `docs/tutorials/`, `docs/reference/`.
- **Contributor guide** — `docs/contributing/`.
- **Per-package style and invariants** — each package's own `CLAUDE.md`.

## Format

One note per file. Filename is the slug (`schema-change-discipline.md`, `authorship-convention.md`, etc.). Each note states: what the rule is, why it exists, and the concrete checklist / commands that enforce it.

Keep them short. If a note grows past one page, it's probably a design decision and belongs in `design/`.
