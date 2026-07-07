# Configurable synthesis roles (PR-K4b)

Status: implemented. Makes the structured-delegation planner's hardcoded role literals
(`document-writer`, `synthesizer`) configurable per workspace/topology, so a swarm can name its
output roles whatever fits its domain and still get the correct planning behavior.

See design §14.3 (compiler), `structured-delegation.md`. Sibling of PR-K3 (`_sentinels.py`), which
removed the status/routing magic strings; this removes the remaining hardcoded **role** nouns the
architecture review flagged.

## Problem

The planner special-cases two kinds of agent by a hardcoded literal:

- **Synthesis/output roles** — `{"self", "document-writer"}`. A task assigned to one of these is
  auto-wired (`TaskPlan.auto_fix_dependencies`) to depend on the research tasks so it runs last, and
  is flagged by `validate_dependencies` if it has no dependencies. A swarm whose final-document agent
  is called `editor` / `publisher` / `composer` silently missed this — its output task ran in
  parallel with research and produced an empty document.
- **The auto-synthesis step role** — `"synthesizer"`, the audit-trace `role` of the large-context
  synthesis call (`_synthesizer.run_synthesis`). A domain noun baked into the trace.

## Decision

Extend the existing `planning:` block (workspace- and topology-level, already home to
`scope_required` / `two_phase`; topology overrides workspace) with two optional fields, resolved into
the frozen `PlanningConfig` and threaded through the compiler. Defaults preserve today's behavior
exactly.

```yaml
runtime:            # (topology) — or top-level for a workspace default
  planning:
    synthesis_roles: [self, document-writer]   # default; roles auto-wired to run last
    synthesizer_role: synthesizer              # default; auto-synthesis trace role
```

- **`self` stays structural.** It is *not* a renamable domain noun — a task assigned to `self` runs
  inline in the coordinator (`_task_executor`), and the `create-task-plan` tool description keys off
  it. `self` is always forced into the resolved `synthesis_roles` (deduped, first) even if a user's
  list omits it. Only the domain nouns (`document-writer`, `synthesizer`) are configurable. This was
  the explicit scope decision (vs. also making `self` an alias — rejected as too large a blast
  radius for no real need).
- **One config surface.** No new schema block; `synthesis_roles` + `synthesizer_role` join the
  established `planning` `$defs` in `topology.schema.json` + `workspace.schema.json`, regenerated
  into the pydantic/TS models.

## Threading

`PlanningConfig.{synthesis_roles, synthesizer_role}` (defaults `DEFAULT_SYNTHESIS_ROLES` /
`DEFAULT_SYNTHESIZER_ROLE` in `_state.py`) →

- `synthesis_roles` → `_task_plan_handler` passes it to `TaskPlan.auto_fix_dependencies` /
  `validate_dependencies` and to `_enforce_two_phase`'s research-vs-output split.
- `synthesizer_role` → `execute_task_batch` → `_maybe_synthesize` → `run_synthesis` →
  `_record_trace`, where it becomes the `AgentStep.role` of the synthesis step.

## Test plan

- Unit (`test_task_plan.py::TestConfigurableSynthesisRoles`): a custom role (`editor`) is auto-wired
  to depend on research when declared and left unwired under the defaults; `validate_dependencies`
  flags it only when declared; `_resolve_planning_config` reads the fields and force-includes `self`.
- Schema: valid fixtures (`topology/with-planning.yaml`, `workspace/with-planning.yaml`) exercise the
  new fields; an invalid fixture (`topology-invalid/bad-synthesis-roles.yaml`) rejects a non-array.
- E2E (`examples/configurable-synthesis-roles/`): a live `swarmkit run` on OpenRouter where the root
  plans three no-dependency tasks; the `editor` task is auto-wired to depend on the research tasks and
  runs in a final batch. Verified against the persisted `tasks.json`.

## Demo

`examples/configurable-synthesis-roles/` — see its README.

## Non-goals

Renaming `self` (structural); making the `__auto_synthesize__` / `__synthesizer__` internal task/id
sentinels configurable (they are internal markers, not domain nouns).
