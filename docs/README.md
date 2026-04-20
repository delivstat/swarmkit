# docs/

User-facing documentation for SwarmKit. The documentation site will be generated from this tree (tooling TBD — MkDocs, Docusaurus, or Nextra are candidates).

## Layout

```
concepts/       # Mental model: topology, agents, skills, archetypes, governance
tutorials/      # Step-by-step: first swarm, authoring a skill, bootstrapping a workspace
reference/      # API / CLI / schema reference (may be partially generated)
contributing/   # Contributor guide, code of conduct, PR workflow
```

## Not here

- **Architecture decisions** — those live in `design/`. Docs reference design sections; they do not duplicate.
- **Runtime README / CLAUDE** — those live inside each `packages/*/`. Docs link to them; they don't copy.
