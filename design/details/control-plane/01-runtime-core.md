# 01 — Runtime core

Scope: `packages/runtime/src/swarmkit_runtime/` — resolver, LangGraph compiler, tool loop,
archetype/skill/topology resolution, planning, synthesis, delegation, eject.

## Run lifecycle (end to end)

`input → resolve workspace → compile topology → execute (tool loop) → output → trace/audit`

1. **Resolve** — `resolver.resolve_workspace(path) -> ResolvedWorkspace` (`resolver/__init__.py:223`).
   Five phases: discover YAML → schema-validate → build skill registry → archetype registry →
   topology resolution (merge agent tree, make all skills concrete) → trigger resolution →
   freeze into immutable `ResolvedWorkspace` (`resolver/_resolved.py:71`: `raw`, `source_path`,
   `topologies`, `skills`, `archetypes`, `triggers`).
2. **Compile** — `WorkspaceRuntime.compile(topology_name)` → `compile_topology(...)`
   (`langgraph_compiler/_compiler.py`) builds a LangGraph `StateGraph` dynamically (topology is
   interpreted, never codegen'd — invariant #1). Wires governance, decision-skill bindings,
   planning + synthesis config, checkpointer.
3. **Execute** — `WorkspaceRuntime.run(topology, input, thread_id=…, previous_plan=…)`
   (`_workspace_runtime.py`) invokes `graph.ainvoke`. Per-agent nodes run the **tool loop**
   (`langgraph_compiler/_tool_loop.py`): model call → tool calls → results → repeat until final
   text or turn cap. Built-in tools dispatched in-loop: `create-scope`/`update-scope`/`read-scope`,
   `read-task-result`, `context_retrieve`, plus delegation (`delegate_to_<child>`) and task-plan tools.
4. **Output + record** — returns `RunResult` (`output`, `events`, `usage: UsageSummary`); writes a
   `RunTrace` to `.swarmkit/traces/<run_id>.json`; persists audit events; archives run-state.

## Public interface

- **CLI:** `swarmkit run`, `swarmkit validate`, `swarmkit chat`, `swarmkit serve` (see [08](08-cli.md)).
- **Programmatic:** `WorkspaceRuntime.from_workspace_path(path)` → `.run()`, `.compile()`, `.resume()`,
  `.judge()` / `.judge_rubric()` (eval), `.start_session()` / `.close()` (MCP lifecycle).
- **Topology keys (config):** `runtime.mode` (`one-shot`/`persistent`/`scheduled`), planning
  (`scope_required`, `two_phase`), `synthesis`, `intent_monitoring`, per-agent `model`/`prompt`/
  `skills`/`iam`/`output_schema`/`children[].depends_on` (DAG). See [07](07-schema.md).
- **Env:** `SWARMKIT_MODEL` / `SWARMKIT_PROVIDER` (override model per run), `SWARMKIT_MAX_TOOLS`,
  `SWARMKIT_MAX_PER_TOOL` / `SWARMKIT_MAX_PER_READ_TOOL`, `SWARMKIT_READ_TOOL_PREFIXES`,
  `SWARMKIT_VERBOSE`, `SWARMKIT_ENV` (env-config selection).

## Extension seams

- **Archetypes** — agent templates (role + default model/prompt/skills/iam); agents instantiate or
  override them. Abstract skill placeholders (`{abstract: {category, capability}}`) resolved at load.
- **Skills** — the only capability primitive; four categories (capability / decision / coordination /
  persistence); implementations: `mcp_tool`, `llm_prompt`, `composed`.
- **Per-run active state** lives in `ContextVar`s (mirrors `set_active_trace`): the active trace,
  compression policy, and original store — so concurrent runs in one process are isolated.

## Data produced & consumed

- Consumes: workspace YAML artifacts (+ `workspace.env*.yaml` interpolation), MCP tool schemas.
- Produces: `RunResult`, `RunTrace`, audit events, run-state (`.swarmkit/run-state/current/{scope,tasks}.json`),
  LangGraph checkpoints (`.swarmkit/state/checkpoints.db`), per-run token + compression stats.

## Control-plane implications

- The compile→run path is per-instance; the panel triggers runs via serve ([02](02-serve-api.md)),
  never by importing the runtime.
- `eject` (codegen) is an **M9 stub** — there is no code-export pipeline yet, so the panel cannot
  offer "export to code" and cannot codegen the compression/auth policy (blocks that deferral).
- Topology resolution is deterministic and fast; a panel could offer remote `validate`/`dry-run`
  by calling serve's `/validate` and `/api/topologies/{id}` (dry_run) — both already exist.
- Per-run `ContextVar` isolation means concurrent serve jobs in one process don't clobber trace/
  compression state — relevant to the connector's concurrency model.
