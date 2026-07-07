# Configurable synthesis roles

Demonstrates `runtime.planning.synthesis_roles` (PR-K4b). By default the structured-delegation
planner treats only `self` and `document-writer` as **synthesis/output roles** — roles that are
auto-wired to depend on the research tasks so they run *last*, not in parallel. This topology
declares a **custom** synthesis role, `editor`:

```yaml
runtime:
  planning:
    synthesis_roles: [self, editor]   # 'editor' now behaves like document-writer
    synthesizer_role: composer        # role label for the auto-synthesis step
```

The root is told to create three tasks with **no** `depends_on` — two `researcher-*` tasks and one
`editor` task. Because `editor` is declared a synthesis role, the planner auto-wires the editor task
to depend on the two research tasks, so it runs in a final batch after the research completes.

## Run it

```bash
export OPENROUTER_API_KEY=...
uv run swarmkit run examples/configurable-synthesis-roles report \
  --input "Write a short report about cats and dogs." --verbose
```

Then inspect the persisted plan — the editor task was auto-wired:

```bash
cat examples/configurable-synthesis-roles/.swarmkit/run-state/*/tasks.json | python -m json.tool
# final-report (agent=editor) depends_on: ["research-cats", "research-dogs", "__auto_synthesize__"]
```

Remove `editor` from `synthesis_roles` and re-run: the editor task then has no auto-dependencies and
runs in parallel with the research tasks (the old hardcoded behavior for any non-`document-writer`
output role).
