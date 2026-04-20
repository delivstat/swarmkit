# swarmkit-ui

Next.js web application providing the three SwarmKit UI surfaces (design §15).

**Scope:** The UI is deferred to v1.1 per the current design decision (§15.3 open question, recommended to confirm in §21). v1.0 ships the authoring swarms via terminal chat mode. This package is scaffolded so work can start whenever the design decision is confirmed; there is no app code yet.

## The three surfaces

| Surface | Purpose | Design ref |
| --- | --- | --- |
| Topology Composer | Design and edit topology files. Structure / Relationships / Network views. | §15.2 |
| Skill Authoring Interface | Conversational front-end to the Skill Authoring Swarm. | §15.1 |
| Runtime Dashboard | Review queues, run history, audit log, skill gap log, catalog browser. | §15.3 |

## Development

```bash
pnpm --filter @swarmkit/ui dev        # dev server at :3000
pnpm --filter @swarmkit/ui build
pnpm --filter @swarmkit/ui test
```
