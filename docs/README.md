# docs/

User-facing documentation for SwarmKit. The documentation site will be generated from this tree (tooling TBD — MkDocs, Docusaurus, or Nextra are candidates).

## Layout

```
concepts/       # Mental model: topology, agents, skills, archetypes, governance
tutorials/      # Step-by-step: first swarm, authoring a skill, bootstrapping a workspace
reference/      # API / CLI / schema reference (may be partially generated)
contributing/   # Contributor guide, code of conduct, PR workflow
notes/          # Cross-cutting discipline / gotcha / "don't forget" notes
```

## Not here

- **Architecture decisions** — those live in `design/`. Docs reference design sections; they do not duplicate.
- **Per-feature design notes** — `design/details/`.
- **Runtime README / CLAUDE** — those live inside each `packages/*/`. Docs link to them; they don't copy.

## `notes/` vs `design/`

- `design/` answers *"what are we building, and why is it shaped this way?"* — architecture, trade-offs, open questions.
- `docs/notes/` answers *"when I make a change, what else do I need to remember?"* — discipline, gotchas, checklists. No decisions recorded here; reminders to apply decisions recorded elsewhere.
