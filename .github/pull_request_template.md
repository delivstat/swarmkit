<!-- Every Swael PR follows this template. See CLAUDE.md "Feature delivery workflow." -->

## Summary

<!-- 1–3 bullets: what this PR does and why. -->

## Design reference

<!-- Link the design note: design/details/<slug>.md — or the section of design/Swael-Design-v0.6.md this implements (e.g. §14.3). -->

## Tests

<!-- What tests were added/updated. Coverage for the new code. Name the key test files. -->
- [ ] Unit tests added / updated
- [ ] Integration tests added / updated (if touching multiple modules)
- [ ] All tests pass locally (`just test`)
- [ ] Lint + typecheck pass (`just lint`, `just typecheck`)

## Demo

<!-- Every feature needs a demo. Paste a terminal transcript, screenshot, screencast link, or reference an examples/ script. -->

```
# Demo output goes here
```

## Invariants checked

<!-- Confirm the CLAUDE.md invariants still hold for this change. Delete lines that don't apply. -->
- [ ] Topology-as-data preserved (no code-gen-as-runtime path introduced)
- [ ] Skills remain the only extension primitive
- [ ] Governance only touched through `GovernanceProvider` (no direct AGT imports outside `governance/`)
- [ ] Audit log append-only (no update/delete exposed to executive callers)
- [ ] Eject story intact (new runtime feature expressible in generated LangGraph)

## Open questions / follow-ups

<!-- Anything deferred, anything §21 of v0.6 that this touches. -->
